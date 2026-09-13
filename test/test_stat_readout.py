"""mechanics.stats.readout - deterministic stat rendering.

A LitRPG-style story shows the player their own numbers, and those numbers have to be
*true*. Asking the model to transcribe them does not work: measured on a real 70-turn
save of the_missing_core, the displayed SYNC read 26 for four consecutive turns while the
save held 34, and the sequence was not even monotonic (28, then 26, 26, 26).

So the model marks the place with a token and the engine substitutes the real values from
state. This is the first feature in the project deliberately taken *away* from the prompt
because it has to be deterministic - see SCHEMA_V2_SPEC.md P-7.

Run directly: python3 test/test_stat_readout.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _llm_stubs import CannedResponses, load_story_engine  # noqa: E402

se = load_story_engine()

READOUT = {
    "token": "[[STATS]]",
    "labels": {"sync": "SYNC", "reach": "REACH", "frame": "FRAME"},
    "entry_format": "**{label}** {value}",
    "separator": " - ",
}


def with_story(ctx, mutate):
    story_dict = se.state_store.thaw(ctx["story"])
    mutate(story_dict)
    ctx["story"] = se.state_store.freeze(story_dict)


ctx = se.state_store.load_state("readouttest", se.state_store.DEFAULT_STORY_SLUG)
ctx["state"]["protagonist"]["stats"] = {"sync": 34, "reach": 40, "frame": 29}

# --- P-2: no readout config means the engine does not touch narration at all ---
with_story(ctx, lambda s: s.setdefault("mechanics", {}).update(stats={"engine": "bounded_counter", "visible": True}))
text = "**[ SYSTEM ]**\n[[STATS]]"
assert se.apply_stat_readouts(ctx, text) == text, \
    "a story with no mechanics.stats.readout must have its narration passed through untouched"
assert se.render_stat_readout(ctx) is None
print("OK: no readout config -> narration untouched, nothing rendered")

# --- the token is replaced with values read from state, in the authored label order ---
with_story(ctx, lambda s: s["mechanics"].update(stats={"engine": "bounded_counter", "visible": True, "readout": READOUT}))
assert se.render_stat_readout(ctx) == "**SYNC** 34 - **REACH** 40 - **FRAME** 29"
assert se.apply_stat_readouts(ctx, "a\n[[STATS]]\nb") == "a\n**SYNC** 34 - **REACH** 40 - **FRAME** 29\nb"
print("OK: token substituted with true values, in the authored label order")

# --- a label with no matching seeded stat is skipped, not rendered as 0 ---
ctx["state"]["protagonist"]["stats"] = {"sync": 5}
assert se.render_stat_readout(ctx) == "**SYNC** 5", "an unseeded stat must be skipped, not zeroed"
ctx["state"]["protagonist"]["stats"] = {"sync": 34, "reach": 40, "frame": 29}
print("OK: a label with no seeded stat is skipped rather than invented as 0")

# --- the backstop: numbers the model wrote by hand anyway are rewritten to the truth ---
stale = "**[ SYSTEM ]**\n*A message.*\n\n**SYNC** 26 - **REACH** 39 - **FRAME** 29\n\nProse after."
fixed = se.apply_stat_readouts(ctx, stale)
assert "**SYNC** 34 - **REACH** 40 - **FRAME** 29" in fixed, "hand-written figures must be corrected"
assert "26" not in fixed and "39" not in fixed, "the stale hand-written values must be gone"
assert fixed.startswith("**[ SYSTEM ]**") and fixed.endswith("Prose after."), \
    "the backstop must rewrite only the figure line, leaving the rest of the scene intact"
print("OK: hand-written stat line is rewritten to the true values, rest of scene intact")

# --- prose is not eaten: one label mention plus an unrelated number is not a stat line ---
prose = "You climb 3 rungs and the SYNC of the hull hums under your glove."
assert se.apply_stat_readouts(ctx, prose) == prose, "a single label in prose must not trigger the backstop"
print("OK: prose mentioning one label and a number is left alone")

# --- the narration prompt tells the model to use the token and never write a number ---
prompt = se.build_system_prompt(ctx)
assert "[[STATS]]" in prompt, "the prompt must name the token the model is supposed to emit"
assert "NEVER write a" in prompt, "the prompt must forbid writing numbers by hand"
with_story(ctx, lambda s: s["mechanics"].update(stats={"engine": "bounded_counter", "visible": True}))
prompt = se.build_system_prompt(ctx)
assert "[[STATS]]" not in prompt, "a visible-stats story with no readout config gets no token instruction"
assert "may be stated directly" in prompt, "it should fall back to the plain visible-stats instruction"
print("OK: token instruction appears only when a readout is configured")

# --- integration: the stored turn carries post-update figures, not pre-update ones ---
with_story(ctx, lambda s: s["mechanics"].update(stats={"engine": "bounded_counter", "visible": True, "readout": READOUT}))
se.call_llm_json = CannedResponses([
    {"subplot_progress": {}, "flags_set": {}, "memory_fragments_revealed": [],
     "inventory": {"gained": [], "used": []}, "new_characters": [], "stat_changes": {"sync": 6},
     "scene_update": {"location": "", "summary": "x", "present_npcs": []}},
])
# with_story deliberately diverges ctx["story"] from the on-disk template, which
# save_state's anti-mutation assert (SCHEMA_V2_SPEC.md §2.2.1) correctly rejects. This
# check is about substitution ordering, not persistence, so persistence is stubbed out.
se.state_store.save_state = lambda *a, **k: None
se.update_state_after_turn(ctx, "an action", "**[ SYSTEM ]**\n[[STATS]]")
stored = ctx["state"]["history"]["recent_turns"][-1]
assert "**SYNC** 40" in stored, \
    f"the stored turn must show the figure the scene ENDED on (34+6=40), got: {stored!r}"
assert "[[STATS]]" not in stored, "no token may survive into stored history"
print("OK: the stored turn shows post-update figures - the readout is the aftermath")

print("\nALL CHECKS PASSED: test_stat_readout")
