"""Regression test for subplot_manager.modify_subplot(): editing a subplot's descriptive
fields (title/description/priority/ties_to_main_plot) in place, without touching
progress/status/active (those have their own dedicated commands with side effects).

Run directly: python3 test/test_modify_subplot.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _llm_stubs import load_story_engine  # noqa: E402

se = load_story_engine()
import subplot_manager  # noqa: E402  (backend/ is already on sys.path via load_story_engine)

ctx = se.state_store.load_state("modifysubplot", se.state_store.DEFAULT_STORY_SLUG)
subplot_id = next(iter(ctx["state"]["plot"]["subplots"]))
original_priority = se._subplot_view(ctx, subplot_id)["priority"]

# --- editing title/description/priority/ties_to_main_plot works ---
subplot_manager.modify_subplot(
    ctx, subplot_id,
    title="A New Title", description="A new description.", priority="high",
    ties_to_main_plot="ties in somehow",
)
view = se._subplot_view(ctx, subplot_id)
assert view["title"] == "A New Title"
assert view["description"] == "A new description."
assert view["priority"] == "high"
assert view["ties_to_main_plot"] == "ties in somehow"
print("OK: modify_subplot updates title/description/priority/ties_to_main_plot")

# --- an unrecognized key is silently ignored, not added ---
subplot_manager.modify_subplot(ctx, subplot_id, not_a_real_field="ignored")
assert "not_a_real_field" not in ctx["state"]["plot"]["subplots"][subplot_id]
print("OK: an unrecognized field is ignored, not added to the subplot")

# --- a field not passed this call is left untouched ---
subplot_manager.modify_subplot(ctx, subplot_id, priority=original_priority)
view = se._subplot_view(ctx, subplot_id)
assert view["priority"] == original_priority
assert view["title"] == "A New Title"
print("OK: fields not passed in a given call are left unchanged")

# --- an unknown subplot id is a no-op, not an error ---
subplot_manager.modify_subplot(ctx, "not_a_real_subplot", title="x")
assert se._subplot_view(ctx, subplot_id)["title"] != "x"
print("OK: an unknown subplot id is a no-op")

print("\nALL CHECKS PASSED: test_modify_subplot")
