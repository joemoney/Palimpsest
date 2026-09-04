"""Regression test for story_engine.generate_missing_options and its wiring into
_generate_and_apply_turn: when a narration reply skips its required OPTIONS block,
take_turn should make one cheap follow-up call_llm call for just the missing block and
append it, rather than silently leaving the player with free-text-only input for that turn.

Run directly: python3 test/test_missing_options_followup.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _llm_stubs import CannedResponses, load_story_engine  # noqa: E402

se = load_story_engine()

ctx_holder = {"ctx": se.state_store.load_state("missingoptionstest", se.state_store.DEFAULT_STORY_SLUG)}
se.state_store.load_state = lambda *a, **k: ctx_holder["ctx"]
se.state_store.save_state = lambda c, *a, **k: ctx_holder.update(ctx=c)

# --- a narration missing its OPTIONS block triggers exactly one follow-up call_llm call,
# and the result is appended so the turn still parses with real options ---
se.call_llm = CannedResponses([
    "Narration with no options block at all.",
    "OPTIONS:\n1. a || a\n2. b || b\n3. c || c",
])
se.call_llm_json = CannedResponses([
    {"subplot_progress": {}, "flags_set": {}, "memory_fragments_revealed": []},
])
se.take_turn("do something")

ctx = ctx_holder["ctx"]
saved_turn = ctx["state"]["history"]["recent_turns"][-1]
assert "Narration with no options block at all." in saved_turn
narration, options = se.parse_narration_and_options(
    saved_turn.split("Narrator: ", 1)[1]
)
assert narration == "Narration with no options block at all."
assert [o["action"] for o in options] == ["a", "b", "c"]
print("OK: a missing OPTIONS block triggers one follow-up call whose result is appended")

# --- if the follow-up call ALSO comes back malformed, the turn still saves cleanly with no
# options (the original documented fallback), rather than raising or looping ---
ctx_holder["ctx"] = se.state_store.load_state("missingoptionstest2", se.state_store.DEFAULT_STORY_SLUG)
se.call_llm = CannedResponses([
    "Narration with no options block, take two.",
    "Still no valid options here.",
])
se.call_llm_json = CannedResponses([
    {"subplot_progress": {}, "flags_set": {}, "memory_fragments_revealed": []},
])
se.take_turn("do something else")

ctx = ctx_holder["ctx"]
saved_turn = ctx["state"]["history"]["recent_turns"][-1]
narration, options = se.parse_narration_and_options(
    saved_turn.split("Narrator: ", 1)[1]
)
assert narration == "Narration with no options block, take two."
assert options == []
print("OK: a follow-up call that's also malformed still degrades to free-text-only, no crash")

print("\nALL CHECKS PASSED: test_missing_options_followup")
