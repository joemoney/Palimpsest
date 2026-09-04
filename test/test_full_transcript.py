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
se.call_llm_json = lambda prompt: {"ready": False, "reason": "not yet"}
se.call_llm = lambda prompt, **kwargs: "updated summary text"

# --- turns still inside the recent_turns window: not archived yet ---
for i in range(1, se.RECENT_TURN_LIMIT + 1):
    se.update_state_after_turn(ctx, f"action {i}", f"narration {i}")

assert len(ctx["state"]["history"]["recent_turns"]) == se.RECENT_TURN_LIMIT
assert ctx["state"]["history"]["full_transcript"] == [], \
    "nothing should be archived yet - all turns are still within the recent_turns window"
print(f"OK: {se.RECENT_TURN_LIMIT} turns held in recent_turns, full_transcript untouched")

# --- one more turn pushes the oldest turn out - that's when it gets archived ---
se.update_state_after_turn(ctx, "action overflow", "narration overflow")

assert len(ctx["state"]["history"]["recent_turns"]) == se.RECENT_TURN_LIMIT
assert len(ctx["state"]["history"]["full_transcript"]) == 1
assert "action 1" in ctx["state"]["history"]["full_transcript"][0]
assert "narration 1" in ctx["state"]["history"]["full_transcript"][0]
assert ctx["state"]["history"]["compressed_summary"] == "updated summary text"
print("OK: oldest turn archived verbatim into full_transcript exactly when it rolled out of "
      "recent_turns and into compressed_summary")

# --- full_transcript must never be fed into a prompt ---
ctx["state"]["history"]["full_transcript"] = ["SENTINEL_SHOULD_NEVER_APPEAR_IN_PROMPT"]
prompt = se.build_system_prompt(ctx)
assert "SENTINEL_SHOULD_NEVER_APPEAR_IN_PROMPT" not in prompt
print("OK: full_transcript is never included in the narration prompt")

# --- missing key on an older save (pre-dating this field) doesn't crash ---
legacy_ctx = se.state_store.load_state("legacyuser", se.state_store.DEFAULT_STORY_SLUG)
legacy_ctx["state"]["history"] = {
    "recent_turns": [f"Player: a{i}\nNarrator: n{i}" for i in range(1, se.RECENT_TURN_LIMIT + 1)],
    "compressed_summary": "",
}  # no "full_transcript" key at all
se.update_state_after_turn(legacy_ctx, "action new", "narration new")
assert legacy_ctx["state"]["history"]["full_transcript"] == ["Player: a1\nNarrator: n1"]
print("OK: missing full_transcript key on a legacy save is created on demand, no crash")

print("\nALL CHECKS PASSED: test_full_transcript")
