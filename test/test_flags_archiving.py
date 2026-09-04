"""Regression test for the flags.active/flags.archive split: non-pinned flags
should age out of flags.active once their setting turn falls outside the
recent-turns window, pinned flags should never age out, and the hard cap
should evict oldest non-pinned flags first when pins pile up.

Run directly: python3 test/test_flags_archiving.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _llm_stubs import load_story_engine  # noqa: E402

se = load_story_engine()

ctx = se.state_store.load_state("flagtest", se.state_store.DEFAULT_STORY_SLUG)
flags = ctx["state"]["protagonist"]["flags"]
flags["active"] = {}
flags["meta"] = {}
flags["archive"] = {}

# --- 1) a pinned flag set long ago must survive archiving ---
ctx["state"]["pacing"]["turn_count"] = 1
flags["active"]["core_identity_hint"] = True
flags["meta"]["core_identity_hint"] = {"turn_set": 1, "pinned": True}

# --- 2) a situational flag set at the same time must NOT survive once it's stale ---
flags["active"]["saw_a_billboard"] = True
flags["meta"]["saw_a_billboard"] = {"turn_set": 1, "pinned": False}

# Fast-forward well past the recent-turns window (RECENT_TURN_LIMIT=10)
ctx["state"]["pacing"]["turn_count"] = 1 + se.RECENT_TURN_LIMIT + 1
se.archive_stale_flags(ctx)

assert "core_identity_hint" in flags["active"], "pinned flag was wrongly archived"
assert "core_identity_hint" not in flags["archive"]
assert "saw_a_billboard" not in flags["active"], "stale non-pinned flag should have been archived"
assert flags["archive"].get("saw_a_billboard") is True
print("OK: pinned flag survives, stale non-pinned flag archived")

# --- 3) a freshly-set non-pinned flag must NOT be archived yet ---
turn_now = ctx["state"]["pacing"]["turn_count"]
flags["active"]["just_happened"] = True
flags["meta"]["just_happened"] = {"turn_set": turn_now, "pinned": False}
se.archive_stale_flags(ctx)
assert "just_happened" in flags["active"], "fresh flag was wrongly archived"
print("OK: freshly-set flag stays active")

# --- 4) hard cap evicts oldest non-pinned first when pins pile up past the window ---
flags["active"].clear()
flags["meta"].clear()
flags["archive"].clear()
turn_now = 1000
ctx["state"]["pacing"]["turn_count"] = turn_now
# all set "now" so none are stale by the age rule - only the hard cap should kick in
for i in range(se.FLAGS_ACTIVE_LIMIT + 5):
    name = f"pinned_fact_{i}"
    flags["active"][name] = True
    flags["meta"][name] = {"turn_set": turn_now - i, "pinned": True}  # all pinned
# one non-pinned flag, oldest of the bunch
flags["active"]["situational_old"] = True
flags["meta"]["situational_old"] = {"turn_set": turn_now - 999, "pinned": False}

se.archive_stale_flags(ctx)
assert "situational_old" not in flags["active"], "hard-cap eviction should prefer non-pinned flags"
assert flags["archive"].get("situational_old") is True
assert len(flags["active"]) == se.FLAGS_ACTIVE_LIMIT + 5, \
    "pinned flags should NOT be evicted even over the cap (only non-pinned are evictable)"
print("OK: hard cap evicts the non-pinned flag first, pinned flags are never evicted")

print("\nALL CHECKS PASSED: test_flags_archiving")
