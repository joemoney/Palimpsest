"""Regression test for the three NPC-creation paths added alongside relationship->NPC
linking:
1. generate_new_subplot/check_and_advance_act can propose a "new_character" alongside the
   subplot/act they generate (via story_engine._maybe_insert_generated_character), committed
   immediately with introduced=False since the character hasn't appeared on the page yet.
2. Manual promotion of an existing relationship-only name via
   story_engine.generate_character_from_relationship + plot_manager.promote_relationship_to_npc
   (see test_inventory_relationships.py for the narration-auto-promotion path, Feature 2).
3. state_store.load_state()'s one-off migration of legacy {name: score} relationships into
   {name: {"score": ..., "npc_id": ...}}.

Run directly: python3 test/test_npc_generation.py
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _llm_stubs import CannedResponses, load_story_engine, load_state_store  # noqa: E402

se = load_story_engine()
import plot_manager  # noqa: E402  (backend/ is already on sys.path via load_story_engine)

# --- generate_new_subplot can propose a new_character, committed with introduced=False ---
state = se.state_store.load_template(se.state_store.DEFAULT_STORY_SLUG)
state["plot"]["subplots"] = {}
state["plot"]["pacing"]["max_parallel_subplots"] = 5
state["characters"] = {}

se.call_llm_json = CannedResponses([
    {"title": "A New Contact", "description": "d", "priority": "medium", "ties_to_main_plot": "t",
     "span": "single_act",
     "new_character": {"name": "Odette Marsh", "description": "a fence with a grudge",
                        "role": "black-market contact", "relationship_to_player": "wary",
                        "hook": "runs the stall behind the fish market"}},
])
se.generate_new_subplot(state)
matches = [c for c in state["characters"].values() if c["name"] == "Odette Marsh"]
assert len(matches) == 1, "generate_new_subplot's new_character should create exactly one NPC"
assert matches[0]["introduced"] is False, "not yet on the page"
assert matches[0]["origin"] == "subplot"
print("OK: generate_new_subplot's new_character commits a real NPC (introduced=False, origin=subplot)")

# A repeated name (already in EXISTING CHARACTERS) must not create a duplicate.
se.call_llm_json = CannedResponses([
    {"title": "Another Thread", "description": "d", "priority": "low", "ties_to_main_plot": "t",
     "span": "single_act", "new_character": {"name": "Odette Marsh", "description": "dup"}},
])
se.generate_new_subplot(state)
assert len([c for c in state["characters"].values() if c["name"] == "Odette Marsh"]) == 1
print("OK: a new_character matching an existing name is not duplicated")

# --- check_and_advance_act can propose a new_character too, only when ready ---
state2 = se.state_store.load_template(se.state_store.DEFAULT_STORY_SLUG)
state2["characters"] = {}
plot2 = state2["plot"]
plot2["pacing"]["subplots_completed_this_act"] = 1  # fast path, triggers the check immediately
plot2["completed_subplots"] = [next(iter(plot2["subplots"]))]

se.call_llm_json = CannedResponses([
    {"ready": True, "reason": "wrapped up", "next_act_title": "Act Two",
     "next_act_description": "The stakes rise.",
     "new_character": {"name": "Warden Ilyc", "description": "runs the new checkpoint",
                        "role": "obstacle", "hook": "guards the only bridge east"}},
])
se.check_and_advance_act(state2)
matches2 = [c for c in state2["characters"].values() if c["name"] == "Warden Ilyc"]
assert len(matches2) == 1
assert matches2[0]["introduced"] is False
assert matches2[0]["origin"] == "act"
print("OK: check_and_advance_act's new_character commits a real NPC (introduced=False, origin=act)")

# A "ready: false" verdict must never insert a character even if new_character is populated.
state3 = se.state_store.load_template(se.state_store.DEFAULT_STORY_SLUG)
state3["characters"] = {}
plot3 = state3["plot"]
plot3["pacing"]["subplots_completed_this_act"] = 1
plot3["completed_subplots"] = [next(iter(plot3["subplots"]))]
se.call_llm_json = CannedResponses([
    {"ready": False, "reason": "not yet", "new_character": {"name": "Should Not Exist"}},
])
se.check_and_advance_act(state3)
assert state3["characters"] == {}
print("OK: check_and_advance_act ignores new_character when the verdict isn't ready")

# --- manual promotion: generate_character_from_relationship + promote_relationship_to_npc ---
state4 = se.state_store.load_template(se.state_store.DEFAULT_STORY_SLUG)
state4["player"]["relationships"] = {
    "the advocate": {"score": 8, "npc_id": None},
    "Sable": {"score": -3, "npc_id": "char_already_linked"},
}
state4["characters"] = {"char_already_linked": {"type": "npc", "name": "Sable", "introduced": True,
                                                  "description": "", "role": "", "hook": "",
                                                  "relationship_to_player": "", "origin": "narration"}}

unlinked = plot_manager.list_unlinked_relationships(state4)
assert unlinked == [("the advocate", 8)], unlinked
print("OK: list_unlinked_relationships excludes already-linked names")

se.call_llm_json = CannedResponses([
    {"description": "a sharp legal mind", "role": "recurring ally",
     "relationship_to_player": "cautiously helpful", "hook": "keeps showing up at hearings"},
])
new_char_id = plot_manager.promote_relationship_to_npc(state4, "the advocate", role="lead counsel")
assert new_char_id is not None
assert state4["characters"][new_char_id]["name"] == "the advocate"
assert state4["characters"][new_char_id]["role"] == "lead counsel"  # override applied
assert state4["characters"][new_char_id]["introduced"] is True
assert state4["characters"][new_char_id]["origin"] == "relationship"
assert state4["player"]["relationships"]["the advocate"]["npc_id"] == new_char_id
print("OK: promote_relationship_to_npc drafts, commits, and links an unlinked relationship")

assert plot_manager.list_unlinked_relationships(state4) == []
print("OK: the promoted relationship no longer shows up as unlinked")

# Promoting an already-linked or unknown name is a no-op, not an error.
assert plot_manager.promote_relationship_to_npc(state4, "Sable") is None
assert plot_manager.promote_relationship_to_npc(state4, "Nobody") is None
print("OK: promoting an already-linked or unknown name is a safe no-op")

# generate_character_from_relationship returns None for an unknown name, and on bad LLM output.
assert se.generate_character_from_relationship(state4, "Nobody") is None
se.call_llm_json = lambda prompt, **kwargs: "not a dict"
assert se.generate_character_from_relationship(state4, "Sable") is None
print("OK: generate_character_from_relationship fails safely on an unknown name or bad output")

# --- state_store migration: legacy {name: score} relationships upgrade on load ---
tmp_dir = tempfile.mkdtemp(prefix="cyoa_npc_migration_test_")
ss = load_state_store(tmp_dir)
user_id, story_slug = "migration-user", "migration-story"
save_path = ss._save_path(user_id, story_slug)
os.makedirs(os.path.dirname(save_path), exist_ok=True)
with open(save_path, "w") as f:
    json.dump({"player": {"relationships": {
        "Legacy Bob": 5,
        "Already Migrated": {"score": 10, "npc_id": "char_001"},
    }}}, f)

migrated = ss.load_state(user_id, story_slug)
assert migrated["player"]["relationships"]["Legacy Bob"] == {"score": 5, "npc_id": None}
assert migrated["player"]["relationships"]["Already Migrated"] == {"score": 10, "npc_id": "char_001"}
print("OK: load_state migrates legacy bare-int relationships and leaves already-migrated ones alone")

print("\nALL CHECKS PASSED: test_npc_generation")
