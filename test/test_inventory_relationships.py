"""Regression test for the state-update pass's inventory and relationship-score tracking
(update_progress_from_turn): items should be added/removed from protagonist.inventory,
relationship deltas should accumulate and clamp to [-100, 100] on
ctx["state"]["characters"][name]["relationship"], and the characters dict should stay
bounded like flags.active - evicting the least significant (closest to neutral) discovered
entries first, not the oldest, once it exceeds RELATIONSHIPS_LIMIT (an authored character,
present in ctx["story"]["world"]["characters"], is never evicted regardless of score).

Run directly: python3 test/test_inventory_relationships.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _llm_stubs import CannedResponses, load_story_engine  # noqa: E402

se = load_story_engine()

ctx = se.state_store.load_state("invreltest", se.state_store.DEFAULT_STORY_SLUG)
ctx["state"]["protagonist"]["inventory"] = []
assert ctx["state"]["characters"] == {}, "a fresh save shouldn't need to predeclare this"

# --- items gained/lost, relationship established ---
se.call_llm_json = CannedResponses([
    {"subplot_progress": {}, "flags_set": {}, "memory_fragments_revealed": [],
     "items_gained": ["a brass key", "a torn letter"], "items_lost": [],
     "relationship_changes": {"Mrs. Abbott": 5}, "new_characters": []},
])
se.update_progress_from_turn(ctx, "take the key", "narration text")
assert ctx["state"]["protagonist"]["inventory"] == ["a brass key", "a torn letter"]
assert ctx["state"]["characters"]["Mrs. Abbott"]["relationship"] == 5
print("OK: items gained and a new relationship score both applied")

# --- an item lost is removed; a relationship delta accumulates onto the existing score ---
se.call_llm_json = CannedResponses([
    {"subplot_progress": {}, "flags_set": {}, "memory_fragments_revealed": [],
     "items_gained": [], "items_lost": ["a torn letter"],
     "relationship_changes": {"Mrs. Abbott": -20}, "new_characters": []},
])
se.update_progress_from_turn(ctx, "hand over the letter", "narration text")
assert ctx["state"]["protagonist"]["inventory"] == ["a brass key"]
assert ctx["state"]["characters"]["Mrs. Abbott"]["relationship"] == -15
print("OK: item removal and relationship delta accumulation both applied")

# --- losing an item not actually held is a safe no-op, not an error ---
se.call_llm_json = CannedResponses([
    {"subplot_progress": {}, "flags_set": {}, "memory_fragments_revealed": [],
     "items_gained": [], "items_lost": ["a sword that was never picked up"], "relationship_changes": {},
     "new_characters": []},
])
se.update_progress_from_turn(ctx, "swing a sword I don't have", "narration text")
assert ctx["state"]["protagonist"]["inventory"] == ["a brass key"]
print("OK: losing an item not in inventory is a safe no-op")

# --- relationship score clamps to [-100, 100] instead of drifting past it ---
ctx["state"]["characters"]["Mrs. Abbott"]["relationship"] = 95
se.call_llm_json = CannedResponses([
    {"subplot_progress": {}, "flags_set": {}, "memory_fragments_revealed": [],
     "items_gained": [], "items_lost": [], "relationship_changes": {"Mrs. Abbott": 50}, "new_characters": []},
])
se.update_progress_from_turn(ctx, "do something wonderful", "narration text")
assert ctx["state"]["characters"]["Mrs. Abbott"]["relationship"] == 100
print("OK: relationship score clamps at the +100 ceiling")

# --- bounded like flags.active: least-significant (closest to neutral) characters evicted first ---
ctx["state"]["characters"] = {
    f"Character {i}": {"relationship": i, "first_seen_turn": 0, "introduced": False}
    for i in range(1, se.RELATIONSHIPS_LIMIT + 1)
}
se.call_llm_json = CannedResponses([
    {"subplot_progress": {}, "flags_set": {}, "memory_fragments_revealed": [],
     "items_gained": [], "items_lost": [], "relationship_changes": {"Newcomer": 40}, "new_characters": []},
])
se.update_progress_from_turn(ctx, "meet someone new", "narration text")
assert len(ctx["state"]["characters"]) == se.RELATIONSHIPS_LIMIT
assert "Character 1" not in ctx["state"]["characters"], "closest-to-neutral should be evicted, not kept"
assert "Newcomer" in ctx["state"]["characters"]
assert f"Character {se.RELATIONSHIPS_LIMIT}" in ctx["state"]["characters"], "strongest relationship must survive"
print(f"OK: characters bounded to {se.RELATIONSHIPS_LIMIT}, weakest relationship evicted first")

# --- an authored character is never evicted, even over budget and at neutral score ---
ctx["state"]["characters"] = {
    f"Character {i}": {"relationship": 0, "first_seen_turn": 0, "introduced": False}
    for i in range(1, se.RELATIONSHIPS_LIMIT + 5)
}
authored_name = next(iter(ctx["state"]["characters"]))
# ctx["story"] is frozen - simulate an authored roster entry by swapping in a thawed,
# re-frozen copy with one added, rather than mutating it in place (which would raise).
story_dict = se.state_store.thaw(ctx["story"])
story_dict["world"]["characters"][authored_name] = {
    "name": authored_name, "description": "An authored character.",
}
ctx["story"] = se.state_store.freeze(story_dict)
se.call_llm_json = CannedResponses([
    {"subplot_progress": {}, "flags_set": {}, "memory_fragments_revealed": [],
     "items_gained": [], "items_lost": [], "relationship_changes": {}, "new_characters": []},
])
se.update_progress_from_turn(ctx, "nothing relationship-related happens", "narration text")
assert authored_name in ctx["state"]["characters"], "an authored character must survive eviction regardless of score"
print("OK: an authored character is never evicted, even at neutral score over budget")

# --- a properly-named new character is created directly, keyed by name (no separate id) ---
ctx2 = se.state_store.load_state("invreltest2", se.state_store.DEFAULT_STORY_SLUG)
se.call_llm_json = CannedResponses([
    {"subplot_progress": {}, "flags_set": {}, "memory_fragments_revealed": [],
     "items_gained": [], "items_lost": [], "relationship_changes": {"Marlowe": 5},
     "new_characters": [{"name": "Marlowe", "description": "a wiry informant", "role": "informant",
                          "relationship_to_player": "guarded", "hook": "reachable by drone"}]},
])
se.update_progress_from_turn(ctx2, "talk to the informant", "narration text")
assert ctx2["state"]["characters"]["Marlowe"]["relationship"] == 5
assert ctx2["state"]["characters"]["Marlowe"]["description"] == "a wiry informant"
assert ctx2["state"]["characters"]["Marlowe"]["introduced"] is True, "already on-page this turn"
assert ctx2["state"]["characters"]["Marlowe"]["origin"] == "narration"
print("OK: a properly-named new_characters entry creates a full record, keyed directly by name")

# --- a generic/descriptive label gets a relationship score only, never a full record ---
se.call_llm_json = CannedResponses([
    {"subplot_progress": {}, "flags_set": {}, "memory_fragments_revealed": [],
     "items_gained": [], "items_lost": [], "relationship_changes": {"the advocate": 3}, "new_characters": []},
])
se.update_progress_from_turn(ctx2, "thank the advocate", "narration text")
assert ctx2["state"]["characters"]["the advocate"]["relationship"] == 3
assert not ctx2["state"]["characters"]["the advocate"].get("description")
print("OK: a generic label is tracked as a relationship only, with no description/role/hook")

# --- a pre-existing character's exact name showing up in relationship_changes just updates
# that same record directly - no separate linking step needed once everything is name-keyed ---
ctx2["state"]["characters"]["Sable"] = {
    "relationship": 0, "first_seen_turn": 0, "introduced": False, "origin": "seed",
}
se.call_llm_json = CannedResponses([
    {"subplot_progress": {}, "flags_set": {}, "memory_fragments_revealed": [],
     "items_gained": [], "items_lost": [], "relationship_changes": {"Sable": 2}, "new_characters": []},
])
se.update_progress_from_turn(ctx2, "greet Sable", "narration text")
assert ctx2["state"]["characters"]["Sable"]["relationship"] == 2
assert ctx2["state"]["characters"]["Sable"]["introduced"] is True
print("OK: relationship_changes for a pre-existing name updates that record and flips introduced")

# --- build_system_prompt includes inventory/relationships without crashing on a fresh save ---
prompt_ctx = se.state_store.load_state("invreltest3", se.state_store.DEFAULT_STORY_SLUG)
assert prompt_ctx["state"]["characters"] == {}
prompt = se.build_system_prompt(prompt_ctx)
assert "Inventory:" in prompt and "Relationships:" in prompt
print("OK: build_system_prompt includes inventory/relationships and tolerates a save with neither yet")

# --- build_system_prompt never leaks internal bookkeeping (first_seen_turn/origin) into the prompt ---
prompt_ctx["state"]["characters"] = {
    "Marlowe": {"relationship": 5, "first_seen_turn": 42, "introduced": True, "origin": "narration"},
}
prompt = se.build_system_prompt(prompt_ctx)
assert "first_seen_turn" not in prompt and "origin" not in prompt
assert "'Marlowe': 5" in prompt or '"Marlowe": 5' in prompt
print("OK: build_system_prompt shows only relationship scores, never internal bookkeeping fields")

print("\nALL CHECKS PASSED: test_inventory_relationships")
