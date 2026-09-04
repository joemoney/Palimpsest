"""Regression test for the fixed opening scene / diegetic name-capture flow in
story_engine.run_opening_scene(): the player should be prompted for a name inside
the scene, the name should land in protagonist.name and get substituted into the
post-name narration, the opening should be logged into recent_turns for LLM
continuity, and replaying it on an already-started game should be a no-op that
never touches input() again.

Run directly: python3 test/test_opening_scene.py
"""
import builtins
import copy
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _llm_stubs import load_story_engine  # noqa: E402

se = load_story_engine()

# state_store.load_state/save_state normally hit disk - redirect both to an
# in-memory copy so this test can't touch any real save file. Patched on the
# state_store module itself (not story_engine) since story_engine now calls
# state_store.load_state(...)/state_store.save_state(...) explicitly.
# ctx["story"] never changes across this test (it's frozen/authored), so it's shared by
# reference rather than deep-copied - only ctx["state"] needs its own copy per scenario.
seed_ctx = se.state_store.load_state("openingtest", se.state_store.DEFAULT_STORY_SLUG)
ctx_holder = {"ctx": {"story": seed_ctx["story"], "state": copy.deepcopy(seed_ctx["state"])}}
ctx_holder["ctx"]["state"]["plot"]["opening_played"] = False
ctx_holder["ctx"]["state"]["protagonist"]["name"] = ""
ctx_holder["ctx"]["state"]["history"]["recent_turns"] = []

se.state_store.load_state = lambda *a, **k: ctx_holder["ctx"]
se.state_store.save_state = lambda c, *a, **k: ctx_holder.update(ctx=c)

# --- first play-through: should prompt for a name and weave it in ---
input_calls = []


def fake_input(prompt=""):
    input_calls.append(prompt)
    return "  Vesper Kade  "  # deliberately padded to confirm it gets stripped


builtins.input = fake_input
se.run_opening_scene()

ctx = ctx_holder["ctx"]
assert len(input_calls) == 1, f"expected exactly one input() call, got {len(input_calls)}"
assert ctx["state"]["protagonist"]["name"] == "Vesper Kade", ctx["state"]["protagonist"]["name"]
assert ctx["state"]["plot"]["opening_played"] is True
assert len(ctx["state"]["history"]["recent_turns"]) == 1
logged = ctx["state"]["history"]["recent_turns"][0]
assert "Vesper Kade" in logged, "the given name should appear in the logged opening turn"
assert "{player_name}" not in logged, "the placeholder token should have been substituted"
print("OK: name captured diegetically, substituted into narration, and logged for continuity")

# --- second call (e.g. resuming a save): must not prompt again ---
def fail_input(prompt=""):
    raise AssertionError("input() should not be called again once the opening has been played")


builtins.input = fail_input
se.run_opening_scene()
assert ctx_holder["ctx"]["state"]["protagonist"]["name"] == "Vesper Kade", "name should be untouched on replay"
print("OK: run_opening_scene() no-ops on an already-started game, no repeat input() call")

# --- blank name falls back to protagonist.default_name ---
default_name = seed_ctx["story"]["protagonist"]["default_name"]
ctx_holder["ctx"] = {"story": seed_ctx["story"], "state": copy.deepcopy(seed_ctx["state"])}
ctx_holder["ctx"]["state"]["plot"]["opening_played"] = False
ctx_holder["ctx"]["state"]["history"]["recent_turns"] = []
builtins.input = lambda prompt="": "   "  # blank / whitespace-only
se.run_opening_scene()
assert ctx_holder["ctx"]["state"]["protagonist"]["name"] == default_name
print(f"OK: blank name input falls back to protagonist.default_name ({default_name!r})")

print("\nALL CHECKS PASSED: test_opening_scene")
