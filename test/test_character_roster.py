"""Regression test for 5.6/CR-06: the top-level characters registry used to be entirely
disconnected from player.relationships - authored NPCs in a template were invisible to the
narrator, and relationship scores attached to whatever free-text name the model chose, with
no canonical identity behind them. The story/state split already merges authored
(ctx["story"]["world"]["characters"]) and discovered (ctx["state"]["characters"]) characters
by name (see story_engine._character_record); this covers the narration prompt's KNOWN
CHARACTERS block that actually surfaces that merge, and the eviction/authored-survival
guarantees around it.

Run directly: python3 test/test_character_roster.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _llm_stubs import load_story_engine  # noqa: E402

se = load_story_engine()

# --- an authored character renders with its description; no score until actually met ---
ctx = se.state_store.load_state("rostertest1", se.state_store.DEFAULT_STORY_SLUG)
prompt = se.build_system_prompt(ctx)
assert "KNOWN CHARACTERS" in prompt
assert "- Mrs. Abbott: Innkeeper at the Harborlight." in prompt
assert "Mrs. Abbott (" not in prompt, "an unmet authored character must not show a score, not even (0)"
print("OK: an authored character renders with its description and no score before being met")

# --- once a relationship entry exists, the score renders alongside it ---
ctx["state"]["characters"]["Mrs. Abbott"] = {"relationship": 12, "first_seen_turn": 1, "introduced": True}
prompt = se.build_system_prompt(ctx)
assert "- Mrs. Abbott (+12): Innkeeper at the Harborlight." in prompt
print("OK: a met authored character shows both its authored description and its live score")

# --- a bare relationship-only stub (no description, not authored) is left out of the roster
# entirely - it has no identity worth restating beyond a name the model itself invented ---
ctx["state"]["characters"]["the advocate"] = {"relationship": 3, "first_seen_turn": 2, "introduced": True}
prompt = se.build_system_prompt(ctx)
assert "the advocate" not in prompt.split("KNOWN CHARACTERS")[1].split("\n\n")[0]
print("OK: a bare generic-label relationship stub is excluded from KNOWN CHARACTERS")

# --- a discovered character with a real description/hook does show up ---
ctx["state"]["characters"]["Marlowe"] = {
    "relationship": -5, "first_seen_turn": 3, "introduced": True, "description": "a wiry fixer",
}
prompt = se.build_system_prompt(ctx)
assert "- Marlowe (-5): a wiry fixer" in prompt
print("OK: a discovered character with a real description appears in the roster too")

# --- stories/example's empty characters dict scenario: no roster block at all ---
empty_ctx = se.state_store.load_state("rostertest2", se.state_store.DEFAULT_STORY_SLUG)
story_dict = se.state_store.thaw(empty_ctx["story"])
story_dict["world"]["characters"] = {}
empty_ctx["story"] = se.state_store.freeze(story_dict)
prompt = se.build_system_prompt(empty_ctx)
assert "KNOWN CHARACTERS" not in prompt
print("OK: an empty characters roster (no authored, no discovered) produces no block")

# --- an authored character is never evicted, surviving in the roster even well past the
# relationship limit and at a neutral score (mirrors test_inventory_relationships' eviction
# coverage, checked here specifically against roster *rendering*) ---
full_ctx = se.state_store.load_state("rostertest3", se.state_store.DEFAULT_STORY_SLUG)
full_ctx["state"]["characters"] = {
    f"Discovered {i}": {"relationship": 0, "first_seen_turn": 0, "introduced": False, "description": f"person {i}"}
    for i in range(1, se.RELATIONSHIPS_LIMIT + 3)
}
prompt = se.build_system_prompt(full_ctx)
assert "Mrs. Abbott" in prompt, "the authored character must still render regardless of how many discovered ones exist"
print("OK: an authored character still renders in the roster alongside many discovered ones")

print("\nALL CHECKS PASSED: test_character_roster")
