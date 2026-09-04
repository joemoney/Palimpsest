"""Regression test for 5.8: a story with an established/historical protagonist can author
opening_scene as {"narration": "..."} instead of the {"narration_before_name",
"narration_after_name"} pair, skipping diegetic name capture entirely and using
protagonist.default_name directly - for a character whose name isn't the player's to choose.

Run directly: python3 test/test_fixed_opening.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _llm_stubs import load_story_engine  # noqa: E402

se = load_story_engine()


def with_story(ctx, mutate):
    story_dict = se.state_store.thaw(ctx["story"])
    mutate(story_dict)
    ctx["story"] = se.state_store.freeze(story_dict)


# --- the existing templates (before/after-name pair) still need capture ---
ctx = se.state_store.load_state("fixedopeningtest", se.state_store.DEFAULT_STORY_SLUG)
assert se.opening_needs_name_capture(ctx) is True
print("OK: a template with narration_before_name/narration_after_name still needs name capture")

# --- switching to the {"narration": "..."} shape skips capture and uses default_name ---
with_story(ctx, lambda s: s["plot"].update(opening_scene={
    "narration": "You are {player_name}, and this story already knows who you are.\n\nOPTIONS:\n"
                 "1. a || a\n2. b || b\n3. c || c",
}))
assert se.opening_needs_name_capture(ctx) is False

text = se.apply_fixed_opening(ctx)
default_name = ctx["story"]["protagonist"]["default_name"]
assert default_name in text
assert "{player_name}" not in text
assert ctx["state"]["protagonist"]["name"] == default_name
assert ctx["state"]["plot"]["opening_played"] is True
assert len(ctx["state"]["history"]["recent_turns"]) == 1
assert default_name in ctx["state"]["history"]["recent_turns"][0]
print(f"OK: apply_fixed_opening applies protagonist.default_name ({default_name!r}) directly, "
      "no capture, and logs the turn for continuity")

print("\nALL CHECKS PASSED: test_fixed_opening")
