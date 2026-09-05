"""Regression test for the busy-popup step vocabulary: story_engine.STATUS_LABELS and
DEFAULT_STEP_ESTIMATE_SECONDS have to stay a mirror of the _timed() call sites, since
that's the only thing keeping app.py's /api/status route from leaking a raw snake_case
label key ("options_generation…") into the player-facing progress popup, or showing a
step with no progress bar at all on a fresh deploy.

Caught for real: generate_missing_options added an "options_generation" _timed() call
inside the turn path without adding it to either dict, so a turn whose narration skipped
its OPTIONS block displayed "options_generation…" until the follow-up call finished.

Run directly: python3 test/test_status_labels.py
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _llm_stubs import load_story_engine  # noqa: E402

se = load_story_engine()

# _timed() labels that can't be reached from take_turn/regenerate_last_turn, so no status
# beacon is ever written for them (see STATUS_LABELS' own comment) - they're mapped for
# display but deliberately carry no progress-bar estimate.
NON_TURN_LABELS = {"steering_seed_generation", "relationship_promotion"}
# Written by app.py before the background thread starts, not by _timed().
APP_WRITTEN_LABELS = {"queued"}

source = open(os.path.join(os.path.dirname(se.__file__), "story_engine.py")).read()
call_site_labels = set(re.findall(r'_timed\(\s*"(\w+)"', source))
assert "narration" in call_site_labels and "state_update" in call_site_labels, call_site_labels

missing_display = call_site_labels - set(se.STATUS_LABELS)
assert not missing_display, f"_timed() labels with no STATUS_LABELS entry: {sorted(missing_display)}"
print("OK: every _timed() call site has a display word in STATUS_LABELS")

stale_display = set(se.STATUS_LABELS) - call_site_labels - APP_WRITTEN_LABELS
assert not stale_display, f"STATUS_LABELS entries with no _timed() call site: {sorted(stale_display)}"
print("OK: STATUS_LABELS has no entries left over from a removed call site")

turn_labels = call_site_labels - NON_TURN_LABELS
missing_estimate = turn_labels - set(se.DEFAULT_STEP_ESTIMATE_SECONDS)
assert not missing_estimate, (
    f"turn-path labels with no DEFAULT_STEP_ESTIMATE_SECONDS seed: {sorted(missing_estimate)}"
)
print("OK: every turn-path step has a seed estimate, so the progress bar shows on a fresh deploy")

stale_estimate = set(se.DEFAULT_STEP_ESTIMATE_SECONDS) - turn_labels
assert not stale_estimate, (
    f"DEFAULT_STEP_ESTIMATE_SECONDS entries that aren't turn-path steps: {sorted(stale_estimate)}"
)
assert all(v > 0 for v in se.DEFAULT_STEP_ESTIMATE_SECONDS.values()), se.DEFAULT_STEP_ESTIMATE_SECONDS
print("OK: DEFAULT_STEP_ESTIMATE_SECONDS carries only turn-path steps, all positive")

# The display words are what the player actually reads - a key echoed back as its own
# value would satisfy the coverage checks above while still looking like a debug string.
for key, word in se.STATUS_LABELS.items():
    assert word != key and "_" not in word, (key, word)
print("OK: every status label is a display word, not the raw key")

print("\nALL CHECKS PASSED: test_status_labels")
