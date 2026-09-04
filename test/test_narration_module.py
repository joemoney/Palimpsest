"""Regression test for the 5.1 narration module: narration.style (moved out of
story_engine.py entirely - a story that omits it gets no PROSE STYLE block, not the old
hardcoded bullets), narration.option_pov (defaults to narration.pov, independently
overridable - both templates pin it to "first-person" since that's what their hand-authored
opening scenes already use, regardless of narration.pov), and narration.option_count
(threaded through both build_system_prompt's footer and parse_narration_and_options's own
minimum-count fallback).

Run directly: python3 test/test_narration_module.py
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


# --- narration.style renders as a PROSE STYLE block; absent means none at all ---
ctx = se.state_store.load_state("narrationmoduletest", se.state_store.DEFAULT_STORY_SLUG)
prompt = se.build_system_prompt(ctx)
assert "PROSE STYLE:" in prompt
assert ctx["story"]["narration"]["style"][0] in prompt

with_story(ctx, lambda s: s["narration"].update(style=[]))
prompt_no_style = se.build_system_prompt(ctx)
assert "PROSE STYLE" not in prompt_no_style
print("OK: narration.style renders as PROSE STYLE, absent means no block at all "
      "(not a fallback to hardcoded bullets)")

# --- narration.option_pov defaults to narration.pov when unset ---
ctx2 = se.state_store.load_state("narrationmoduletest2", se.state_store.DEFAULT_STORY_SLUG)
with_story(ctx2, lambda s: (s["narration"].pop("option_pov", None), s["narration"].update(pov="third-person")))
prompt = se.build_system_prompt(ctx2)
assert "third-person prose rendition" in prompt
print("OK: narration.option_pov defaults to narration.pov when not explicitly set")

# --- both templates explicitly pin option_pov to first-person (matching their authored
# opening scenes), independent of pov ---
for slug in ("example", "new_babel"):
    ctx3 = se.state_store.load_state(f"narrationmoduletest_{slug}", slug)
    assert ctx3["story"]["narration"]["pov"] == "second-person"
    assert ctx3["story"]["narration"]["option_pov"] == "first-person"
    prompt = se.build_system_prompt(ctx3)
    assert "first-person prose rendition" in prompt
    assert "second-person prose rendition" not in prompt
print("OK: both templates override option_pov to first-person independent of pov")

# --- narration.option_count threads through the footer instruction ---
ctx4 = se.state_store.load_state("narrationmoduletest4", se.state_store.DEFAULT_STORY_SLUG)
with_story(ctx4, lambda s: s["narration"].update(option_count=4))
prompt = se.build_system_prompt(ctx4)
assert "exactly 4 numbered options (1. / 2. / 3. / 4.)" in prompt
print("OK: narration.option_count threads through the footer's numbered-options instruction")

# --- narration.option_count also threads through parse_narration_and_options's own
# minimum-count fallback, independent of the prompt-building side ---
four_option_text = (
    "Narration text.\n\nOPTIONS:\n"
    "1. a || a\n2. b || b\n3. c || c\n4. d || d"
)
narration, options = se.parse_narration_and_options(four_option_text, option_count=4)
assert len(options) == 4, options
narration2, options2 = se.parse_narration_and_options(four_option_text, option_count=3)
assert len(options2) == 3, options2  # capped, not just filtered
three_option_text = "Narration text.\n\nOPTIONS:\n1. a || a\n2. b || b\n3. c || c"
narration3, options3 = se.parse_narration_and_options(three_option_text, option_count=4)
assert options3 == [], "fewer options than option_count should fall back to no buttons at all"
print("OK: parse_narration_and_options respects option_count as both a cap and a minimum")

print("\nALL CHECKS PASSED: test_narration_module")
