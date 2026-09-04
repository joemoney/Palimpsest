"""Regression test for the character-creation paths:
1. generate_new_subplot/check_and_advance_act can propose a "new_character" alongside the
   subplot/act they generate (via story_engine._maybe_insert_generated_character), committed
   immediately with introduced=False since the character hasn't appeared on the page yet -
   written straight into ctx["state"]["characters"][name], keyed by name (see
   test_inventory_relationships.py for the narration-auto-creation path).
2. Manual promotion of an existing relationship-only name via
   story_engine.generate_character_from_relationship + plot_manager.promote_relationship_to_npc.
3. migrate_v1.migrate()'s upgrade of a v1 save's relationships (both the old bare-int shape
   and the newer {"score", "npc_id"} shape, and the separate NPC-record registry they used
   to point at) into v2's single characters store.

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
ctx = se.state_store.load_state("npctest1", se.state_store.DEFAULT_STORY_SLUG)
ctx["state"]["plot"]["subplots"] = {}

se.call_llm_json = CannedResponses([
    {"title": "A New Contact", "description": "d", "priority": "medium", "ties_to_main_plot": "t",
     "span": "single_act",
     "new_character": {"name": "Odette Marsh", "description": "a fence with a grudge",
                        "role": "black-market contact", "relationship_to_player": "wary",
                        "hook": "runs the stall behind the fish market"}},
])
se.generate_new_subplot(ctx)
assert "Odette Marsh" in ctx["state"]["characters"]
record = ctx["state"]["characters"]["Odette Marsh"]
assert record["introduced"] is False, "not yet on the page"
assert record["origin"] == "subplot"
print("OK: generate_new_subplot's new_character commits a real character (introduced=False, origin=subplot)")

# A repeated name (already in EXISTING CHARACTERS) must not create a duplicate/overwrite.
se.call_llm_json = CannedResponses([
    {"title": "Another Thread", "description": "d", "priority": "low", "ties_to_main_plot": "t",
     "span": "single_act", "new_character": {"name": "Odette Marsh", "description": "dup"}},
])
se.generate_new_subplot(ctx)
assert len(ctx["state"]["characters"]) == 1
assert ctx["state"]["characters"]["Odette Marsh"]["description"] == "a fence with a grudge", \
    "an existing name must not be overwritten by a duplicate new_character"
print("OK: a new_character matching an existing name is not duplicated")

# --- check_and_advance_act can propose a new_character too, only when ready ---
ctx2 = se.state_store.load_state("npctest2", se.state_store.DEFAULT_STORY_SLUG)
pacing2 = ctx2["state"]["pacing"]
pacing2["subplots_completed_this_act"] = 1  # fast path, triggers the check immediately
ctx2["state"]["plot"]["completed_subplots"] = [next(iter(ctx2["state"]["plot"]["subplots"]))]

se.call_llm_json = CannedResponses([
    {"ready": True, "reason": "wrapped up", "next_act_title": "Act Two",
     "next_act_description": "The stakes rise.", "completion_signals": ["a signal"],
     "new_character": {"name": "Warden Ilyc", "description": "runs the new checkpoint",
                        "role": "obstacle", "hook": "guards the only bridge east"}},
])
se.check_and_advance_act(ctx2)
assert "Warden Ilyc" in ctx2["state"]["characters"]
record2 = ctx2["state"]["characters"]["Warden Ilyc"]
assert record2["introduced"] is False
assert record2["origin"] == "act"
print("OK: check_and_advance_act's new_character commits a real character (introduced=False, origin=act)")

# A "ready: false" verdict must never insert a character even if new_character is populated.
ctx3 = se.state_store.load_state("npctest3", se.state_store.DEFAULT_STORY_SLUG)
pacing3 = ctx3["state"]["pacing"]
pacing3["subplots_completed_this_act"] = 1
ctx3["state"]["plot"]["completed_subplots"] = [next(iter(ctx3["state"]["plot"]["subplots"]))]
se.call_llm_json = CannedResponses([
    {"ready": False, "reason": "not yet", "new_character": {"name": "Should Not Exist"}},
])
se.check_and_advance_act(ctx3)
assert ctx3["state"]["characters"] == {}
print("OK: check_and_advance_act ignores new_character when the verdict isn't ready")

# --- manual promotion: generate_character_from_relationship + promote_relationship_to_npc ---
ctx4 = se.state_store.load_state("npctest4", se.state_store.DEFAULT_STORY_SLUG)
ctx4["state"]["characters"] = {
    "the advocate": {"relationship": 8, "first_seen_turn": 0, "introduced": False},
    "Sable": {"relationship": -3, "first_seen_turn": 0, "introduced": True,
              "description": "already a full record", "origin": "narration"},
}

unlinked = plot_manager.list_unlinked_relationships(ctx4)
assert unlinked == [("the advocate", 8)], unlinked
print("OK: list_unlinked_relationships excludes an already-full character record")

se.call_llm_json = CannedResponses([
    {"description": "a sharp legal mind", "role": "recurring ally",
     "relationship_to_player": "cautiously helpful", "hook": "keeps showing up at hearings"},
])
result_name = plot_manager.promote_relationship_to_npc(ctx4, "the advocate", role="lead counsel")
assert result_name == "the advocate"
assert ctx4["state"]["characters"]["the advocate"]["role"] == "lead counsel"  # override applied
assert ctx4["state"]["characters"]["the advocate"]["introduced"] is True
assert ctx4["state"]["characters"]["the advocate"]["origin"] == "relationship"
print("OK: promote_relationship_to_npc drafts and commits a full record directly onto the existing entry")

assert plot_manager.list_unlinked_relationships(ctx4) == []
print("OK: the promoted relationship no longer shows up as unlinked")

# Promoting an already-full or unknown name is a no-op, not an error.
assert plot_manager.promote_relationship_to_npc(ctx4, "Sable") is None
assert plot_manager.promote_relationship_to_npc(ctx4, "Nobody") is None
print("OK: promoting an already-full or unknown name is a safe no-op")

# generate_character_from_relationship returns None for an unknown name, and on bad LLM output.
assert se.generate_character_from_relationship(ctx4, "Nobody") is None
se.call_llm_json = lambda prompt, **kwargs: "not a dict"
assert se.generate_character_from_relationship(ctx4, "Sable") is None
print("OK: generate_character_from_relationship fails safely on an unknown name or bad output")

# --- migrate_v1.migrate(): legacy relationships (both shapes) upgrade into v2's characters ---
tmp_dir = tempfile.mkdtemp(prefix="cyoa_npc_migration_test_")
ss = load_state_store(tmp_dir)
user_id, story_slug = "migration-user", "migration-story"

story_dir = os.path.join(ss.STORIES_DIR, story_slug)
os.makedirs(story_dir, exist_ok=True)
with open(os.path.join(story_dir, "template.json"), "w") as f:
    json.dump({
        "schema_version": 2, "story_version": "test.1",
        "meta": {"title": "Migration Story", "genre": "test"},
        "narration": {}, "world": {"rules": []}, "protagonist": {}, "mechanics": {},
        "plot": {
            "main_thread": {"title": "t", "description": "d", "acts": [
                {"act_number": 1, "title": "Act 1", "description": "d", "completion_signals": []}
            ]},
            "subplots": {}, "pacing": {"nudge_frequency": 8, "act_check_frequency": 12, "max_parallel_subplots": 3},
            "opening_scene": {"narration_before_name": "", "narration_after_name": ""},
            "initial_scene": {"location": "", "summary": ""},
        },
    }, f)

save_path = ss._save_path(user_id, story_slug)
os.makedirs(os.path.dirname(save_path), exist_ok=True)
with open(save_path, "w") as f:
    json.dump({
        "player": {
            "name": "Tester", "traits": [], "inventory": [], "stats": {},
            "flags_active": {}, "flags_meta": {}, "flags_archive": {}, "creation_choices": {},
            "origin": {"memory_fragments": []},
            "relationships": {
                "Legacy Bob": 5,  # pre-npc_id bare-int shape
                "Already Migrated": {"score": 10, "npc_id": "char_001"},
            },
        },
        "characters": {
            "char_001": {"type": "npc", "name": "Already Migrated", "introduced": True,
                         "description": "a full record", "role": "ally",
                         "relationship_to_player": "friendly", "hook": "", "origin": "narration"},
        },
        "plot": {
            "main_thread": {"current_act": 1, "acts": [
                {"act_number": 1, "title": "Act 1", "description": "d", "completed": False, "optional": False}
            ], "act_history": [], "emergent_directions": []},
            "thread_steering": {"last_pivot_turn": 0, "pivot_history": [], "emerging_themes": [], "player_driven_goals": []},
            "subplots": {}, "completed_subplots": [], "entity_interaction_count": 0,
            "endgame": {"requested": False, "requested_turn": None, "final_arc": None, "concluded": False},
            "pacing": {"turn_count": 0, "turns_since_last_pacing_nudge": 0, "pacing_nudge_frequency": 8,
                       "turns_since_last_act_check": 0, "act_check_frequency": 12, "max_parallel_subplots": 3,
                       "subplots_completed_this_act": 0, "last_pacing_direction": ""},
            "current_scene": {"location": "", "summary": "", "present_npcs": []},
            "opening_scene": {"played": False, "narration_before_name": "", "narration_after_name": ""},
        },
        "history_log": {"recent_turns": [], "compressed_summary": "", "full_transcript": []},
    }, f)

migrated_ctx = ss.load_state(user_id, story_slug)
characters = migrated_ctx["state"]["characters"]
assert characters["Legacy Bob"]["relationship"] == 5
assert characters["Already Migrated"]["relationship"] == 10
assert characters["Already Migrated"]["description"] == "a full record"
assert characters["Already Migrated"]["introduced"] is True
print("OK: migrate_v1.migrate() upgrades legacy bare-int and npc_id-linked relationships alike, "
      "merging the linked NPC record's fields onto the same entry")

print("\nALL CHECKS PASSED: test_npc_generation")
