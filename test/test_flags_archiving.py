"""Regression test for the flags_active/flags_archive split: non-pinned flags
should age out of flags_active once their setting turn falls outside the
recent-turns window, pinned flags should never age out, and the hard cap
should evict oldest non-pinned flags first when pins pile up.

Run directly: python3 test/test_flags_archiving.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _llm_stubs import load_story_engine  # noqa: E402

se = load_story_engine()

state = se.state_store.load_template(se.state_store.DEFAULT_STORY_SLUG)
player = state["player"]
player["flags_active"] = {}
player["flags_meta"] = {}
player["flags_archive"] = {}

# --- 1) a pinned flag set long ago must survive archiving ---
state["plot"]["pacing"]["turn_count"] = 1
player["flags_active"]["core_identity_hint"] = True
player["flags_meta"]["core_identity_hint"] = {"turn_set": 1, "pinned": True}

# --- 2) a situational flag set at the same time must NOT survive once it's stale ---
player["flags_active"]["saw_a_billboard"] = True
player["flags_meta"]["saw_a_billboard"] = {"turn_set": 1, "pinned": False}

# Fast-forward well past the recent-turns window (RECENT_TURN_LIMIT=10)
state["plot"]["pacing"]["turn_count"] = 1 + se.RECENT_TURN_LIMIT + 1
se.archive_stale_flags(state)

assert "core_identity_hint" in player["flags_active"], "pinned flag was wrongly archived"
assert "core_identity_hint" not in player["flags_archive"]
assert "saw_a_billboard" not in player["flags_active"], "stale non-pinned flag should have been archived"
assert player["flags_archive"].get("saw_a_billboard") is True
print("OK: pinned flag survives, stale non-pinned flag archived")

# --- 3) a freshly-set non-pinned flag must NOT be archived yet ---
turn_now = state["plot"]["pacing"]["turn_count"]
player["flags_active"]["just_happened"] = True
player["flags_meta"]["just_happened"] = {"turn_set": turn_now, "pinned": False}
se.archive_stale_flags(state)
assert "just_happened" in player["flags_active"], "fresh flag was wrongly archived"
print("OK: freshly-set flag stays active")

# --- 4) hard cap evicts oldest non-pinned first when pins pile up past the window ---
player["flags_active"].clear()
player["flags_meta"].clear()
player["flags_archive"].clear()
turn_now = 1000
state["plot"]["pacing"]["turn_count"] = turn_now
# all set "now" so none are stale by the age rule - only the hard cap should kick in
for i in range(se.FLAGS_ACTIVE_LIMIT + 5):
    name = f"pinned_fact_{i}"
    player["flags_active"][name] = True
    player["flags_meta"][name] = {"turn_set": turn_now - i, "pinned": True}  # all pinned
# one non-pinned flag, oldest of the bunch
player["flags_active"]["situational_old"] = True
player["flags_meta"]["situational_old"] = {"turn_set": turn_now - 999, "pinned": False}

se.archive_stale_flags(state)
assert "situational_old" not in player["flags_active"], "hard-cap eviction should prefer non-pinned flags"
assert player["flags_archive"].get("situational_old") is True
assert len(player["flags_active"]) == se.FLAGS_ACTIVE_LIMIT + 5, \
    "pinned flags should NOT be evicted even over the cap (only non-pinned are evictable)"
print("OK: hard cap evicts the non-pinned flag first, pinned flags are never evicted")

print("\nALL CHECKS PASSED: test_flags_archiving")
