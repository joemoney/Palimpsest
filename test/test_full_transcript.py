"""Regression test for history.full_transcript: it should archive a turn's
full text only once that turn overflows recent_turns and would otherwise be
lossy-summarized away - not on every turn - and it must never be read back
into an LLM prompt.

Run directly: python3 test/test_full_transcript.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _llm_stubs import load_story_engine  # noqa: E402

se = load_story_engine()

ctx = se.state_store.load_state("fulltranscript", se.state_store.DEFAULT_STORY_SLUG)
ctx["state"]["history"]["recent_turns"] = []
ctx["state"]["history"]["compressed_summary"] = ""
ctx["state"]["history"]["full_transcript"] = []

se.state_store.save_state = lambda c, *a, **k: None
se.call_llm_json = lambda prompt, **kwargs: {"ready": False, "reason": "not yet"}
se.call_llm = lambda prompt, **kwargs: "updated summary text"

# --- turns still inside the recent_turns window: not archived yet ---
for i in range(1, se.RECENT_TURN_LIMIT + 1):
    se.update_state_after_turn(ctx, f"action {i}", f"narration {i}")

assert len(ctx["state"]["history"]["recent_turns"]) == se.RECENT_TURN_LIMIT
assert ctx["state"]["history"]["full_transcript"] == [], \
    "nothing should be archived yet - all turns are still within the recent_turns window"
print(f"OK: {se.RECENT_TURN_LIMIT} turns held in recent_turns, full_transcript untouched")

# --- one turn past the window archives NOTHING: rollover is batched ---
# Regression guard. This used to archive (and re-summarize) on every single turn past the
# tenth, because the trigger was "longer than RECENT_TURN_LIMIT" and each turn appends
# exactly one. See ROLLOVER_BATCH_TURNS in story_engine.
se.update_state_after_turn(ctx, "action overflow", "narration overflow")

assert len(ctx["state"]["history"]["recent_turns"]) == se.RECENT_TURN_LIMIT + 1
assert ctx["state"]["history"]["full_transcript"] == [], \
    "one turn past the prompt window must not trigger a rollover - that is the every-turn bug"
assert ctx["state"]["history"]["compressed_summary"] == ""
print("OK: one turn past RECENT_TURN_LIMIT archives nothing - rollover waits for a full batch")

# --- reaching the batch threshold archives the whole batch at once ---
# Counted, not conditioned on len(recent_turns): the rollover trims the list back inside the
# very call that tips it over, so a "while shorter than the threshold" loop never exits.
played = se.RECENT_TURN_LIMIT + 1  # the window-filling turns above, plus the overflow one
for n in range(played + 1, se.RECENT_TURN_LIMIT + se.ROLLOVER_BATCH_TURNS + 1):
    se.update_state_after_turn(ctx, f"action {n}", f"narration {n}")

assert len(ctx["state"]["history"]["recent_turns"]) == se.RECENT_TURN_LIMIT
assert len(ctx["state"]["history"]["full_transcript"]) == se.ROLLOVER_BATCH_TURNS
assert "action 1" in ctx["state"]["history"]["full_transcript"][0]
assert "narration 1" in ctx["state"]["history"]["full_transcript"][0]
assert ctx["state"]["history"]["compressed_summary"] == "updated summary text"
print(f"OK: hitting the batch threshold archived all {se.ROLLOVER_BATCH_TURNS} oldest turns "
      "verbatim into full_transcript and folded them into compressed_summary")

# --- full_transcript must never be fed into a prompt ---
ctx["state"]["history"]["full_transcript"] = ["SENTINEL_SHOULD_NEVER_APPEAR_IN_PROMPT"]
prompt = se.build_system_prompt(ctx)
assert "SENTINEL_SHOULD_NEVER_APPEAR_IN_PROMPT" not in prompt
print("OK: full_transcript is never included in the narration prompt")

# --- missing key on an older save (pre-dating this field) doesn't crash ---
legacy_ctx = se.state_store.load_state("legacyuser", se.state_store.DEFAULT_STORY_SLUG)
legacy_ctx["state"]["history"] = {
    # One short of the batch threshold, so the turn below tips it over and exercises the
    # create-on-demand path rather than returning before it.
    "recent_turns": [f"Player: a{i}\nNarrator: n{i}"
                     for i in range(1, se.RECENT_TURN_LIMIT + se.ROLLOVER_BATCH_TURNS)],
    "compressed_summary": "",
}  # no "full_transcript" key at all
se.update_state_after_turn(legacy_ctx, "action new", "narration new")
assert legacy_ctx["state"]["history"]["full_transcript"][0] == "Player: a1\nNarrator: n1"
assert len(legacy_ctx["state"]["history"]["full_transcript"]) == se.ROLLOVER_BATCH_TURNS
print("OK: missing full_transcript key on a legacy save is created on demand, no crash")

print("\nALL CHECKS PASSED: test_full_transcript")
