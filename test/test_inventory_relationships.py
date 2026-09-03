"""Regression test for the state-update pass's inventory and relationship-score tracking
(update_progress_from_turn): items should be added/removed from player.inventory, relationship
deltas should accumulate and clamp to [-100, 100] on player.relationships[name]["score"], and
the relationships dict should stay bounded like flags_active - evicting the least significant
(closest to neutral) entries first, not the oldest, once it exceeds RELATIONSHIPS_LIMIT.

Run directly: python3 test/test_inventory_relationships.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _llm_stubs import CannedResponses, load_story_engine  # noqa: E402

se = load_story_engine()

state = se.state_store.load_template(se.state_store.DEFAULT_STORY_SLUG)
state["player"]["inventory"] = []
assert "relationships" not in state["player"], "template shouldn't need to predeclare this - setdefault handles it"

# --- items gained/lost, relationship established ---
se.call_llm_json = CannedResponses([
    {"subplot_progress": {}, "flags_set": {}, "memory_fragments_revealed": [], "entity_interaction": False,
     "items_gained": ["a brass key", "a torn letter"], "items_lost": [],
     "relationship_changes": {"Mrs. Abbott": 5}, "new_characters": []},
])
se.update_progress_from_turn(state, "take the key", "narration text")
assert state["player"]["inventory"] == ["a brass key", "a torn letter"]
assert state["player"]["relationships"]["Mrs. Abbott"]["score"] == 5
assert state["player"]["relationships"]["Mrs. Abbott"]["npc_id"] is None
print("OK: items gained and a new relationship score both applied")

# --- an item lost is removed; a relationship delta accumulates onto the existing score ---
se.call_llm_json = CannedResponses([
    {"subplot_progress": {}, "flags_set": {}, "memory_fragments_revealed": [], "entity_interaction": False,
     "items_gained": [], "items_lost": ["a torn letter"],
     "relationship_changes": {"Mrs. Abbott": -20}, "new_characters": []},
])
se.update_progress_from_turn(state, "hand over the letter", "narration text")
assert state["player"]["inventory"] == ["a brass key"]
assert state["player"]["relationships"]["Mrs. Abbott"]["score"] == -15
print("OK: item removal and relationship delta accumulation both applied")

# --- losing an item not actually held is a safe no-op, not an error ---
se.call_llm_json = CannedResponses([
    {"subplot_progress": {}, "flags_set": {}, "memory_fragments_revealed": [], "entity_interaction": False,
     "items_gained": [], "items_lost": ["a sword that was never picked up"], "relationship_changes": {},
     "new_characters": []},
])
se.update_progress_from_turn(state, "swing a sword I don't have", "narration text")
assert state["player"]["inventory"] == ["a brass key"]
print("OK: losing an item not in inventory is a safe no-op")

# --- relationship score clamps to [-100, 100] instead of drifting past it ---
state["player"]["relationships"]["Mrs. Abbott"]["score"] = 95
se.call_llm_json = CannedResponses([
    {"subplot_progress": {}, "flags_set": {}, "memory_fragments_revealed": [], "entity_interaction": False,
     "items_gained": [], "items_lost": [], "relationship_changes": {"Mrs. Abbott": 50}, "new_characters": []},
])
se.update_progress_from_turn(state, "do something wonderful", "narration text")
assert state["player"]["relationships"]["Mrs. Abbott"]["score"] == 100
print("OK: relationship score clamps at the +100 ceiling")

# --- bounded like flags_active: least-significant (closest to neutral) relationships evicted first ---
state["player"]["relationships"] = {
    f"Character {i}": {"score": i, "npc_id": None} for i in range(1, se.RELATIONSHIPS_LIMIT + 1)
}
se.call_llm_json = CannedResponses([
    {"subplot_progress": {}, "flags_set": {}, "memory_fragments_revealed": [], "entity_interaction": False,
     "items_gained": [], "items_lost": [], "relationship_changes": {"Newcomer": 40}, "new_characters": []},
])
se.update_progress_from_turn(state, "meet someone new", "narration text")
assert len(state["player"]["relationships"]) == se.RELATIONSHIPS_LIMIT
assert "Character 1" not in state["player"]["relationships"], "closest-to-neutral should be evicted, not kept"
assert "Newcomer" in state["player"]["relationships"]
assert f"Character {se.RELATIONSHIPS_LIMIT}" in state["player"]["relationships"], "strongest relationship must survive"
print(f"OK: relationships bounded to {se.RELATIONSHIPS_LIMIT}, weakest evicted first")

# --- a properly-named new character is auto-promoted to a real NPC and linked ---
state["player"]["relationships"] = {}
state["characters"] = {}
se.call_llm_json = CannedResponses([
    {"subplot_progress": {}, "flags_set": {}, "memory_fragments_revealed": [], "entity_interaction": False,
     "items_gained": [], "items_lost": [], "relationship_changes": {"Marlowe": 5},
     "new_characters": [{"name": "Marlowe", "description": "a wiry informant", "role": "informant",
                          "relationship_to_player": "guarded", "hook": "reachable by drone"}]},
])
se.update_progress_from_turn(state, "talk to the informant", "narration text")
assert state["player"]["relationships"]["Marlowe"]["score"] == 5
npc_id = state["player"]["relationships"]["Marlowe"]["npc_id"]
assert npc_id is not None, "a properly-named new_characters entry should be linked immediately"
assert state["characters"][npc_id]["name"] == "Marlowe"
assert state["characters"][npc_id]["introduced"] is True, "already on-page this turn"
assert state["characters"][npc_id]["origin"] == "narration"
print("OK: a properly-named new_characters entry creates and links a real NPC")

# --- a generic/descriptive label gets a relationship score only, never an automatic NPC ---
se.call_llm_json = CannedResponses([
    {"subplot_progress": {}, "flags_set": {}, "memory_fragments_revealed": [], "entity_interaction": False,
     "items_gained": [], "items_lost": [], "relationship_changes": {"the advocate": 3}, "new_characters": []},
])
se.update_progress_from_turn(state, "thank the advocate", "narration text")
assert state["player"]["relationships"]["the advocate"]["score"] == 3
assert state["player"]["relationships"]["the advocate"]["npc_id"] is None
print("OK: a generic label is tracked as a relationship only, with no automatic NPC")

# --- an existing NPC's exact name showing up in relationship_changes gets linked/backfilled ---
state["characters"]["char_099"] = {
    "type": "npc", "name": "Sable", "description": "", "role": "", "relationship_to_player": "",
    "hook": "", "introduced": False, "origin": "seed",
}
se.call_llm_json = CannedResponses([
    {"subplot_progress": {}, "flags_set": {}, "memory_fragments_revealed": [], "entity_interaction": False,
     "items_gained": [], "items_lost": [], "relationship_changes": {"Sable": 2}, "new_characters": []},
])
se.update_progress_from_turn(state, "greet Sable", "narration text")
assert state["player"]["relationships"]["Sable"]["npc_id"] == "char_099"
assert state["characters"]["char_099"]["introduced"] is True
print("OK: an exact-name match against an existing NPC still backfills the link and introduced flag")

# --- build_system_prompt includes inventory/relationships without crashing on old saves ---
prompt_state = se.state_store.load_template(se.state_store.DEFAULT_STORY_SLUG)
assert "relationships" not in prompt_state["player"]
prompt = se.build_system_prompt(prompt_state)
assert "Inventory:" in prompt and "Relationships:" in prompt
print("OK: build_system_prompt includes inventory/relationships and tolerates a save with neither yet")

# --- build_system_prompt never leaks npc_id into the prompt text ---
prompt_state["player"]["relationships"] = {"Marlowe": {"score": 5, "npc_id": "char_001"}}
prompt = se.build_system_prompt(prompt_state)
assert "npc_id" not in prompt and "char_001" not in prompt
assert "'Marlowe': 5" in prompt or '"Marlowe": 5' in prompt
print("OK: build_system_prompt shows only relationship scores, never npc_id")

print("\nALL CHECKS PASSED: test_inventory_relationships")
