"""Regression test for the state-update pass's inventory and relationship-score tracking
(update_progress_from_turn): items should be added/removed from protagonist.inventory,
relationship scores should accumulate and clamp to the story's authored scale on
ctx["state"]["characters"][name]["relationship"], and the characters dict should stay
bounded like flags.active - evicting the least significant (closest to neutral) discovered
entries first, not the oldest, once it exceeds the engine's `limit` (an authored character,
present in ctx["story"]["world"]["characters"], is never evicted regardless of score).

Phase 4 note: the relationship half of this file goes through the `scored_axis` engine now,
so a turn reports *what happened socially* and the template's price list turns that into a
number. The deltas below are the example story's authored register values, not magnitudes
this test chose - see backend/mechanics/social.py, and test_relationships_axis.py for the
engine's own rules.

Run directly: python3 test/test_inventory_relationships.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _llm_stubs import CannedResponses, load_story_engine  # noqa: E402

se = load_story_engine()
LIMIT = se.mechanics.social.DEFAULT_LIMIT
KINDNESS = 4        # example's registers.kindness_shown
BEHIND_BACK = -9    # example's registers.went_behind_their_back
CONFIDED = 9        # example's registers.confided_in_them


def social(target, register, reciprocated=True):
    return [{"target": target, "register": register, "reciprocated": reciprocated}]


def labels(ctx):
    """Item labels in order. Phase 4: the inventory holds records, not bare strings, and
    the ids are minted by the engine - so a test asserting on contents asserts on what the
    player would see, not on bookkeeping it does not own."""
    return [se.mechanics.items.TaggedItems._record(e).get("label")
            for e in ctx["state"]["protagonist"]["inventory"]]


def item_ids(ctx):
    return [se.mechanics.items.TaggedItems._record(e).get("id")
            for e in ctx["state"]["protagonist"]["inventory"]]


ctx = se.state_store.load_state("invreltest", se.state_store.DEFAULT_STORY_SLUG)
ctx["state"]["protagonist"]["inventory"] = []
assert ctx["state"]["characters"] == {}, "a fresh save shouldn't need to predeclare this"

# --- items gained/lost, relationship established ---
se.call_llm_json = CannedResponses([
    {"subplot_progress": {}, "flags_set": {}, "memory_fragments_revealed": [],
     "inventory": {"gained": [{"label": "a brass key", "tags": ["key"]},
                              {"label": "a torn letter", "tags": ["document"]}], "used": []},
     "social": social("Mrs. Abbott", "kindness_shown"), "new_characters": []},
])
se.update_progress_from_turn(ctx, "take the key", "narration text")
assert labels(ctx) == ["a brass key", "a torn letter"]
assert item_ids(ctx) == ["itm_001", "itm_002"], item_ids(ctx)
assert ctx["state"]["characters"]["Mrs. Abbott"]["relationship"] == KINDNESS
print("OK: items gained (as records, with minted ids) and a new relationship score applied")

letter_id = item_ids(ctx)[1]

# --- an item lost is removed; a relationship delta accumulates onto the existing score ---
se.call_llm_json = CannedResponses([
    {"subplot_progress": {}, "flags_set": {}, "memory_fragments_revealed": [],
     "inventory": {"gained": [], "used": [letter_id]},
     "social": social("Mrs. Abbott", "went_behind_their_back"), "new_characters": []},
])
se.update_progress_from_turn(ctx, "hand over the letter", "narration text")
assert labels(ctx) == ["a brass key"]
assert ctx["state"]["characters"]["Mrs. Abbott"]["relationship"] == KINDNESS + BEHIND_BACK
print("OK: item removal and relationship delta accumulation both applied")

# --- citing an id that is not held is a safe no-op, not an error. This is what replaced
# v2's exact-string match against the inventory, and it has to fail the same harmless way:
# an unmatched expenditure leaves the inventory alone rather than raising or removing
# something adjacent. ---
se.call_llm_json = CannedResponses([
    {"subplot_progress": {}, "flags_set": {}, "memory_fragments_revealed": [],
     "inventory": {"gained": [], "used": ["itm_404", "a sword that was never picked up"]},
     "social": [], "new_characters": []},
])
se.update_progress_from_turn(ctx, "swing a sword I don't have", "narration text")
assert labels(ctx) == ["a brass key"]
print("OK: expending an id (or label) that is not held is a safe no-op")

# --- relationship score clamps to the authored scale instead of drifting past it ---
ctx["state"]["characters"]["Mrs. Abbott"]["relationship"] = 95
se.call_llm_json = CannedResponses([
    {"subplot_progress": {}, "flags_set": {}, "memory_fragments_revealed": [],
     "inventory": {"gained": [], "used": []}, "social": social("Mrs. Abbott", "confided_in_them"),
     "new_characters": []},
])
se.update_progress_from_turn(ctx, "do something wonderful", "narration text")
assert 95 + CONFIDED > 100, "this check only means something if the raw sum would overshoot"
assert ctx["state"]["characters"]["Mrs. Abbott"]["relationship"] == 100
print("OK: relationship score clamps at the story's +100 ceiling")

# --- bounded like flags.active: least-significant (closest to neutral) characters evicted first ---
ctx["state"]["characters"] = {
    f"Character {i}": {"relationship": i, "first_seen_turn": 0, "introduced": False}
    for i in range(1, LIMIT + 1)
}
se.call_llm_json = CannedResponses([
    {"subplot_progress": {}, "flags_set": {}, "memory_fragments_revealed": [],
     "inventory": {"gained": [], "used": []}, "social": social("Newcomer", "confided_in_them"),
     "new_characters": []},
])
se.update_progress_from_turn(ctx, "meet someone new", "narration text")
assert len(ctx["state"]["characters"]) == LIMIT
assert "Character 1" not in ctx["state"]["characters"], "closest-to-neutral should be evicted, not kept"
assert "Newcomer" in ctx["state"]["characters"], (
    "a newcomer must be scored before the roster is measured, not dropped on arrival")
assert f"Character {LIMIT}" in ctx["state"]["characters"], "strongest relationship must survive"
print(f"OK: characters bounded to {LIMIT}, weakest relationship evicted first")

# --- an authored character is never evicted, even over budget and at neutral score ---
ctx["state"]["characters"] = {
    f"Character {i}": {"relationship": 0, "first_seen_turn": 0, "introduced": False}
    for i in range(1, LIMIT + 5)
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
     "inventory": {"gained": [], "used": []}, "social": [], "new_characters": []},
])
se.update_progress_from_turn(ctx, "nothing relationship-related happens", "narration text")
assert authored_name in ctx["state"]["characters"], "an authored character must survive eviction regardless of score"
print("OK: an authored character is never evicted, even at neutral score over budget")

# --- a properly-named new character is created directly, keyed by name (no separate id) ---
ctx2 = se.state_store.load_state("invreltest2", se.state_store.DEFAULT_STORY_SLUG)
se.call_llm_json = CannedResponses([
    {"subplot_progress": {}, "flags_set": {}, "memory_fragments_revealed": [],
     "inventory": {"gained": [], "used": []}, "social": social("Marlowe", "kindness_shown"),
     "new_characters": [{"name": "Marlowe", "description": "a wiry informant", "role": "informant",
                          "relationship_to_player": "guarded", "hook": "reachable by drone"}]},
])
se.update_progress_from_turn(ctx2, "talk to the informant", "narration text")
assert ctx2["state"]["characters"]["Marlowe"]["relationship"] == KINDNESS
assert ctx2["state"]["characters"]["Marlowe"]["description"] == "a wiry informant"
assert ctx2["state"]["characters"]["Marlowe"]["introduced"] is True, "already on-page this turn"
assert ctx2["state"]["characters"]["Marlowe"]["origin"] == "narration"
print("OK: a properly-named new_characters entry creates a full record, keyed directly by name")

# --- a generic/descriptive label gets a relationship score only, never a full record ---
se.call_llm_json = CannedResponses([
    {"subplot_progress": {}, "flags_set": {}, "memory_fragments_revealed": [],
     "inventory": {"gained": [], "used": []}, "social": social("the advocate", "kindness_shown"),
     "new_characters": []},
])
se.update_progress_from_turn(ctx2, "thank the advocate", "narration text")
assert ctx2["state"]["characters"]["the advocate"]["relationship"] == KINDNESS
assert not ctx2["state"]["characters"]["the advocate"].get("description")
print("OK: a generic label is tracked as a relationship only, with no description/role/hook")

# --- a pre-existing character's exact name showing up in a social beat just updates that
# same record directly - no separate linking step needed once everything is name-keyed ---
ctx2["state"]["characters"]["Sable"] = {
    "relationship": 0, "first_seen_turn": 0, "introduced": False, "origin": "seed",
}
se.call_llm_json = CannedResponses([
    {"subplot_progress": {}, "flags_set": {}, "memory_fragments_revealed": [],
     "inventory": {"gained": [], "used": []}, "social": social("Sable", "kindness_shown"),
     "new_characters": []},
])
se.update_progress_from_turn(ctx2, "greet Sable", "narration text")
assert ctx2["state"]["characters"]["Sable"]["relationship"] == KINDNESS
assert ctx2["state"]["characters"]["Sable"]["introduced"] is True
print("OK: a social beat for a pre-existing name updates that record and flips introduced")

# --- build_system_prompt tolerates a fresh save with neither inventory nor a roster.
# Phase 4: an empty roster contributes no Relationships fragment at all rather than
# "Relationships: {}" - a zeroed header is what P-2 forbids, and the engine omits the whole
# section rather than rendering an empty one. ---
prompt_ctx = se.state_store.load_state("invreltest3", se.state_store.DEFAULT_STORY_SLUG)
assert prompt_ctx["state"]["characters"] == {}
prompt = se.build_system_prompt(prompt_ctx)
assert "Inventory:" in prompt
assert "Relationships:" not in prompt, "an empty roster must not render a zeroed header (P-2)"
print("OK: build_system_prompt keeps inventory and omits an empty Relationships block")

# --- build_system_prompt never leaks internal bookkeeping (first_seen_turn/origin) into the prompt ---
prompt_ctx["state"]["characters"] = {
    "Marlowe": {"relationship": 5, "first_seen_turn": 42, "introduced": True, "origin": "narration"},
}
prompt = se.build_system_prompt(prompt_ctx)
assert "first_seen_turn" not in prompt and "origin" not in prompt
assert "Relationships: Marlowe +5" in prompt, prompt
print("OK: build_system_prompt shows only relationship scores, never internal bookkeeping fields")

print("\nALL CHECKS PASSED: test_inventory_relationships")
