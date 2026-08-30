"""Regression test for freeform LLM-assisted steering seeds: turning a background note into
a draft character/subplot/direction (story_engine.generate_steering_seed,
plot_manager.stage_steering_seed/apply_steering_seed/discard_steering_seed) that the player
reviews before anything is committed. Also covers the fix that makes both seeded AND
hand-authored steering content (add_emergent_direction, add_player_goal) actually reach
generate_pacing_nudge, instead of sitting write-only in the save the way they did before.

Run directly: python3 test/test_steering_seed.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _llm_stubs import load_story_engine, CannedResponses  # noqa: E402

se = load_story_engine()
import plot_manager  # noqa: E402  (backend/ is already on sys.path via load_story_engine)

state = se.state_store.load_template(se.state_store.DEFAULT_STORY_SLUG)

# --- character-type seed: stage, apply with an override, auto-introduce, nudge surfacing ---
responses = CannedResponses([
    # 1) stage_steering_seed -> generate_steering_seed
    {"type": "character", "character": {
        "name": "Vesper Kade", "description": "A wary courier with old debts.",
        "role": "potential ally", "relationship_to_player": "guarded",
        "hook": "Crosses paths with the player at the next supply drop.",
    }},
    # 2) update_progress_from_turn's state-update pass, once the player meets her
    {"relationship_changes": {"Vesper Kade": 5}},
])
se.call_llm_json = responses

seed_id = plot_manager.stage_steering_seed(state, "Add a new character who could become an ally.")
assert seed_id is not None
pending = state["plot"]["thread_steering"]["pending_seeds"]
assert len(pending) == 1 and pending[0]["id"] == seed_id and pending[0]["type"] == "character"
print("OK: stage_steering_seed stages a character draft for review, nothing else touched")

char_id = plot_manager.apply_steering_seed(state, seed_id, role="close confidant")
assert char_id is not None
assert state["plot"]["thread_steering"]["pending_seeds"] == []
char = state["characters"][char_id]
assert char["name"] == "Vesper Kade"
assert char["role"] == "close confidant"  # override wins over the generated draft
assert char["introduced"] is False
print("OK: apply_steering_seed commits the character with an override; pending seed cleared")

nudge = se.generate_pacing_nudge(state)
assert "Vesper Kade" in nudge and "supply drop" in nudge, nudge
print("OK: generate_pacing_nudge surfaces an un-introduced seeded character's hook")

se.update_progress_from_turn(state, "talk to Vesper", "narration mentioning Vesper Kade")
assert state["characters"][char_id]["introduced"] is True
print("OK: meeting the character in a turn auto-flips introduced via relationship_changes")

nudge_after = se.generate_pacing_nudge(state)
assert "Vesper Kade" not in nudge_after, nudge_after
print("OK: an introduced character stops being surfaced")

assert responses.remaining() == 0

# --- subplot-type seed: reuses story_engine.insert_subplot ---
responses = CannedResponses([
    {"type": "subplot", "subplot": {
        "title": "Salvage Run", "description": "A risky job surfaces.",
        "priority": "high", "ties_to_main_plot": "The salvage ties back to the main thread.",
    }},
])
se.call_llm_json = responses

seed_id = plot_manager.stage_steering_seed(state, "Add a subplot about a risky salvage job.")
subplot_id = plot_manager.apply_steering_seed(state, seed_id)
assert subplot_id in state["plot"]["subplots"]
assert state["plot"]["subplots"][subplot_id]["title"] == "Salvage Run"
assert state["plot"]["thread_steering"]["pending_seeds"] == []
print("OK: subplot-type seed applies via the shared insert_subplot path")
assert responses.remaining() == 0

# --- direction-type seed: lands in emergent_directions, now actually read by the nudge ---
responses = CannedResponses([
    {"type": "direction", "direction": {
        "title": "A Debt Comes Due", "description": "An old debt might resurface.",
    }},
])
se.call_llm_json = responses

seed_id = plot_manager.stage_steering_seed(state, "Note that an old debt might resurface.")
result = plot_manager.apply_steering_seed(state, seed_id)
assert result is None  # directions have no id of their own, unlike character/subplot
directions = state["plot"]["main_thread"]["emergent_directions"]
assert any(d["title"] == "A Debt Comes Due" and not d["promoted"] for d in directions)
print("OK: direction-type seed lands in emergent_directions, unpromoted")

nudge = se.generate_pacing_nudge(state)
assert "A Debt Comes Due" in nudge, nudge
print("OK: generate_pacing_nudge surfaces the seeded direction")
assert responses.remaining() == 0

# --- discard: drops a pending seed without applying it ---
responses = CannedResponses([
    {"type": "direction", "direction": {"title": "Throwaway", "description": "Never mind."}},
])
se.call_llm_json = responses

seed_id = plot_manager.stage_steering_seed(state, "some note")
before = len(state["plot"]["main_thread"]["emergent_directions"])
plot_manager.discard_steering_seed(state, seed_id)
assert state["plot"]["thread_steering"]["pending_seeds"] == []
assert len(state["plot"]["main_thread"]["emergent_directions"]) == before
print("OK: discard_steering_seed drops a draft without committing it")
assert responses.remaining() == 0

# --- unknown seed id is a no-op, not an error ---
assert plot_manager.apply_steering_seed(state, "not_a_real_seed") is None
plot_manager.discard_steering_seed(state, "not_a_real_seed")  # should not raise
print("OK: an unknown seed id is a no-op for both apply and discard")

# --- malformed/unexpected LLM output yields None, same failure mode as generate_new_subplot ---
se.call_llm_json = lambda prompt, **kwargs: {"not_type": "oops"}
assert se.generate_steering_seed(state, "a note") is None
print("OK: malformed LLM output yields None instead of raising")

# --- regression: hand-authored steering (not seed-generated) now also reaches the nudge ---
state2 = se.state_store.load_template(se.state_store.DEFAULT_STORY_SLUG)
plot_manager.add_emergent_direction(
    state2, "The Undercity Signal", "A recurring signal hints at something buried."
)
plot_manager.add_player_goal(state2, "Find out who's been intercepting the mail.")
nudge2 = se.generate_pacing_nudge(state2)
assert "The Undercity Signal" in nudge2, nudge2
assert "Find out who's been intercepting the mail." in nudge2, nudge2
print("OK: hand-authored add-emergent/add-goal now reach generate_pacing_nudge (write-only fix)")

print("\nALL CHECKS PASSED: test_steering_seed")
