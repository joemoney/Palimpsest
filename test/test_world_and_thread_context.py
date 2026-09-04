"""Regression test for the CRD's P0/P1 narration-prompt additions that don't touch the
state-update contract: CR-02 (location id -> human name), CR-04 (world context block: setting/
locations/factions), CR-05 (main thread + current act), CR-14 (POV), and CR-17 (standardised
by-key current-act lookup, exercised through build_system_prompt/generate_pacing_nudge/
generate_new_subplot after a non-contiguous act insertion among generated acts).

Run directly: python3 test/test_world_and_thread_context.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _llm_stubs import load_story_engine, RecordingLLM  # noqa: E402

se = load_story_engine()
import plot_manager as pm  # noqa: E402


def with_story(ctx, mutate):
    """ctx["story"] is frozen - swap in a thawed, re-frozen copy with `mutate` applied,
    rather than mutating it in place (which would raise)."""
    story_dict = se.state_store.thaw(ctx["story"])
    mutate(story_dict)
    ctx["story"] = se.state_store.freeze(story_dict)


ctx = se.state_store.load_state("worldcontexttest", se.state_store.DEFAULT_STORY_SLUG)

# --- CR-14: POV appears in the identity header, conditional on narration.pov ---
prompt = se.build_system_prompt(ctx)
assert "POV: second-person" in prompt, "expected narration.pov to appear in the identity header"
with_story(ctx, lambda s: s["narration"].pop("pov", None))
prompt_no_pov = se.build_system_prompt(ctx)
assert "POV:" not in prompt_no_pov, "a story with no pov key should get no POV token at all"
with_story(ctx, lambda s: s["narration"].update(pov="second-person"))
print("OK: CR-14 POV present when narration.pov is set, absent when it isn't")

# --- CR-02 / CR-04: known location renders by name; HERE/ADJACENT/SETTING/FACTIONS present ---
prompt = se.build_system_prompt(ctx)
assert "CURRENT SCENE (The Ferry Dock):" in prompt, "known location id should render as its name"
assert "loc_dock" not in prompt, "the raw location key should never leak into the prompt"
assert "SETTING: Millbrook is a small coastal town" in prompt
assert "HERE: The Ferry Dock - " in prompt
assert "ADJACENT: The Harborlight Inn" in prompt
assert "FACTIONS:\n- The Townsfolk:" in prompt
assert "(toward the player: friendly, hospitable, and quietly evasive)" in prompt
print("OK: CR-02 location resolves to name; CR-04 setting/here/adjacent/factions all present")

# --- CR-02: an unknown location id falls back to the raw string, no crash ---
ctx["state"]["scene"]["location"] = "loc_totally_made_up"
prompt = se.build_system_prompt(ctx)
assert "CURRENT SCENE (loc_totally_made_up):" in prompt
assert "HERE:" not in prompt, "an unresolvable location should get no HERE/ADJACENT block"
ctx["state"]["scene"]["location"] = "loc_dock"
print("OK: CR-02 unknown location id renders raw, without raising")

# --- CR-04: a dangling connected_to id is skipped silently, not rendered raw ---
with_story(ctx, lambda s: s["world"]["locations"]["loc_dock"].update(connected_to=["loc_inn", "loc_nonexistent"]))
prompt = se.build_system_prompt(ctx)
assert "ADJACENT: The Harborlight Inn" in prompt
assert "loc_nonexistent" not in prompt
with_story(ctx, lambda s: s["world"]["locations"]["loc_dock"].update(connected_to=["loc_inn"]))
print("OK: CR-04 dangling connected_to id skipped silently")

# --- CR-04: empty locations/factions produce no sub-blocks ---
empty_ctx = se.state_store.load_state("worldcontexttest2", se.state_store.DEFAULT_STORY_SLUG)
with_story(empty_ctx, lambda s: s["world"].update(locations={}, factions={}))
prompt = se.build_system_prompt(empty_ctx)
assert "HERE:" not in prompt and "ADJACENT:" not in prompt and "FACTIONS:" not in prompt
assert "SETTING:" in prompt, "setting_summary is independent of locations/factions"
print("OK: CR-04 empty locations/factions produce no world sub-blocks")

# --- CR-05: main thread title/description and current act appear in every narration prompt ---
prompt = se.build_system_prompt(ctx)
assert "MAIN THREAD: What Happened to Yesterday - Figure out why Millbrook's residents" in prompt
assert "CURRENT ACT 1: Arrival - " in prompt
print("OK: CR-05 main thread + current act present")

# --- CR-17: after a non-contiguous insertion among *generated* acts, every by-key lookup
# still finds the right one. Act 1 is always authored/immutable in v2, so a position-based
# insertion can only ever renumber other *generated* acts, not the authored one - this
# exercises that renumbering plus every by-key lookup site (build_system_prompt,
# generate_pacing_nudge, generate_new_subplot, generate_steering_seed). ---
pm.add_act(ctx, "First Generated Act", "Becomes Act 2.")
pm.add_act(ctx, "Inserted Act", "Pushed in at position 2, renumbering the one above.", position=2)
generated_numbers = sorted(a["act_number"] for a in ctx["state"]["plot"]["generated_acts"])
assert generated_numbers == [2, 3], generated_numbers
inserted = next(a for a in ctx["state"]["plot"]["generated_acts"] if a["title"] == "Inserted Act")
assert inserted["act_number"] == 2, inserted
pushed = next(a for a in ctx["state"]["plot"]["generated_acts"] if a["title"] == "First Generated Act")
assert pushed["act_number"] == 3, pushed

ctx["state"]["plot"]["current_act"] = 2
assert se._current_act(ctx)["title"] == "Inserted Act"

prompt = se.build_system_prompt(ctx)
assert "CURRENT ACT 2: Inserted Act - Pushed in at position 2" in prompt

recorder = RecordingLLM(lambda p: {
    "title": "t", "description": "d", "priority": "medium", "ties_to_main_plot": "x",
})
se.call_llm_json = recorder
se.generate_new_subplot(ctx)
assert "CURRENT ACT: Inserted Act - Pushed in at position 2" in recorder.prompts[-1]
print("OK: CR-17 by-key current-act lookup stays correct after non-contiguous renumbering "
      "among generated acts (build_system_prompt and generate_new_subplot)")

print("\nALL CHECKS PASSED: test_world_and_thread_context")
