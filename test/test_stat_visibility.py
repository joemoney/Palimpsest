"""Regression test for 5.4: mechanics.stats.visible is a per-story dial deciding whether the
narrator may quote a stat's raw number to the player, mirroring how mechanics.stats.floor/
.ceiling (5.2) replaced the global STAT_FLOOR constant.

The default must stay False: every story predating this dial relies on the opaque-stats
instruction, and a LitRPG-style story whose premise is an in-world system reporting the
player's own figures back to them needs the exact opposite. Both the PLAYER line's label and
the prompt footer's instruction have to flip together, or the narrator gets contradictory
guidance in the same prompt.

Run directly: python3 test/test_stat_visibility.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _llm_stubs import load_story_engine  # noqa: E402

se = load_story_engine()

OPAQUE_LABEL = "Stats (opaque to the player)"
SHOWN_LABEL = "Stats (SHOWN to the player by this story)"
OPAQUE_RULE = "never state a stat's raw numeric value to the player"
SHOWN_RULE = "raw numeric values may be stated directly"


def with_story(ctx, mutate):
    story_dict = se.state_store.thaw(ctx["story"])
    mutate(story_dict)
    ctx["story"] = se.state_store.freeze(story_dict)


ctx = se.state_store.load_state("statvistest", se.state_store.DEFAULT_STORY_SLUG)
ctx["state"]["protagonist"]["stats"] = {"health": 40}

# --- no mechanics.stats at all: the old opaque behaviour, unchanged ---
with_story(ctx, lambda s: s.get("mechanics", {}).pop("stats", None))
prompt = se.build_system_prompt(ctx)
assert OPAQUE_LABEL in prompt and SHOWN_LABEL not in prompt, \
    "a story with no mechanics.stats must keep the opaque label"
assert OPAQUE_RULE in prompt and SHOWN_RULE not in prompt, \
    "a story with no mechanics.stats must keep the opaque instruction"
print("OK: no mechanics.stats at all leaves stats opaque, matching pre-5.4 behaviour")

# --- mechanics.stats authored but visible absent: still defaults to opaque ---
with_story(ctx, lambda s: s.setdefault("mechanics", {}).update(stats={"floor": 0, "ceiling": 100}))
prompt = se.build_system_prompt(ctx)
assert OPAQUE_LABEL in prompt and OPAQUE_RULE in prompt, \
    "mechanics.stats without an explicit visible key must still default to opaque"
print("OK: mechanics.stats without 'visible' defaults to opaque, so floor/ceiling stays orthogonal")

# --- visible: true flips both the label and the instruction together ---
with_story(ctx, lambda s: s["mechanics"].update(stats={"floor": 0, "ceiling": 100, "visible": True}))
prompt = se.build_system_prompt(ctx)
assert SHOWN_LABEL in prompt and OPAQUE_LABEL not in prompt, \
    "visible: true must flip the PLAYER line's label"
assert SHOWN_RULE in prompt and OPAQUE_RULE not in prompt, \
    "visible: true must flip the footer instruction - a prompt carrying both is contradictory"
print("OK: visible: true flips the PLAYER-line label and the footer instruction together")

# --- visible: false is explicitly honoured, not just falsy-by-absence ---
with_story(ctx, lambda s: s["mechanics"].update(stats={"floor": 0, "visible": False}))
prompt = se.build_system_prompt(ctx)
assert OPAQUE_LABEL in prompt and OPAQUE_RULE in prompt, "an explicit visible: false must stay opaque"
print("OK: an explicit visible: false is honoured")

# --- a story with no stats at all gets neither instruction, whatever the dial says ---
ctx["state"]["protagonist"]["stats"] = {}
with_story(ctx, lambda s: s["mechanics"].update(stats={"visible": True}))
prompt = se.build_system_prompt(ctx)
assert OPAQUE_LABEL not in prompt and SHOWN_LABEL not in prompt, \
    "a story with no stats must get no stats label, even with visible: true"
assert OPAQUE_RULE not in prompt and SHOWN_RULE not in prompt, \
    "a story with no stats must get no stats instruction clutter, even with visible: true"
print("OK: a story with no stats gets no label or instruction regardless of the dial")

print("\nALL CHECKS PASSED: test_stat_visibility")
