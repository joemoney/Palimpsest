"""A second bug found alongside the act-advancement one (2026-09-28), same shape: a subplot added
to the template after a save has already started was invisible to that save forever, because
`state["plot"]["subplots"]` is only ever seeded once, at save creation
(`state_store.new_save_state`). Every reader - `mechanics/threads.py`'s `all_subplots`
("ctx['state']['plot']['subplots'] is the authoritative id set"), `apply_thread_conditions`,
generation, the pacing nudge's SUBPLOT OPPORTUNITY line - only ever looks at that dict, so a
thread the author adds mid-playthrough needed a fresh save to ever run.

`state_store._reconcile` (already the documented seam for "the template changed since this save
started, make the runtime state agree" - SCHEMA_V2_SPEC.md §2.3) now instantiates a newly-seen
subplot the same way `new_save_state` does, on every load. No fresh save needed.

Run directly: python3 test/test_subplot_reconcile.py
"""
import copy
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _llm_stubs import load_state_store, load_story_engine  # noqa: E402

TMP = tempfile.mkdtemp(prefix="cyoa_subplot_reconcile_test_")
ss = load_state_store(TMP)

STORY = {
    "schema_version": 3, "story_version": "2024-01-01.1", "meta": {"title": "T"},
    "narration": {"pov": "second-person"}, "world": {"setting_summary": "s", "rules": ["r"]},
    "protagonist": {"default_name": "T"},
    "plot": {"main_thread": {"title": "M", "description": "d", "acts": [{"act_number": 1, "title": "A", "description": "d"}]},
             "initial_scene": {"location": "start", "summary": "s"},
             "opening_scene": {"narration_before_name": "b", "narration_after_name": "a"},
             "subplots": {"s1": {"title": "First", "description": "d", "starts_active": True}}},
    "mechanics": {"subplots": {"engine": "weighted_threads"}},
}


def write(slug, story):
    d = os.path.join(ss.STORIES_DIR, slug)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "template.json"), "w") as f:
        json.dump(story, f)


write("reconcile_test", STORY)
ctx = ss.load_state("recon_user", "reconcile_test")
assert set(ctx["state"]["plot"]["subplots"]) == {"s1"}
ss.save_state(ctx, "recon_user", "reconcile_test")

# author adds two threads mid-playthrough: one starts active, one waits on a condition
grown = copy.deepcopy(STORY)
grown["plot"]["subplots"]["s2"] = {"title": "Second", "description": "d", "starts_active": True}
grown["plot"]["subplots"]["s3"] = {"title": "Third", "description": "d", "activate_when": {"turn_gte": 5}}
write("reconcile_test", grown)

ctx2 = ss.load_state("recon_user", "reconcile_test")
subplots = ctx2["state"]["plot"]["subplots"]
assert set(subplots) == {"s1", "s2", "s3"}, subplots
assert subplots["s2"] == {"progress": 0, "status": "active", "active": True}
assert subplots["s3"] == {"progress": 0, "status": "not_started", "active": False}
print("OK: a subplot added to the template appears in an existing save's runtime state on its next load, seeded exactly as a fresh save would seed it")

# an already-tracked subplot is untouched, even if the player has moved it (progress, status)
subplots["s1"]["progress"] = 42
subplots["s1"]["status"] = "progressed"
ss.save_state(ctx2, "recon_user", "reconcile_test")
ctx3 = ss.load_state("recon_user", "reconcile_test")
assert ctx3["state"]["plot"]["subplots"]["s1"] == {"progress": 42, "status": "progressed", "active": True}
print("OK: an already-tracked subplot's progress is never touched by reconciliation")

# a fresh save (created after the growth) already has everything - reconciliation is a no-op for it
fresh = ss.load_state("recon_user2", "reconcile_test")
assert set(fresh["state"]["plot"]["subplots"]) == {"s1", "s2", "s3"}
print("OK: a brand-new save already has every authored subplot; nothing left for reconciliation to add")

# it threads through into play: apply_thread_conditions activates s3 once its condition holds
se = load_story_engine()
se.state_store.STORIES_DIR, se.state_store.STORIES_PRIVATE_DIR = ss.STORIES_DIR, ss.STORIES_PRIVATE_DIR
se.state_store.DATA_DIR, se.state_store.SAVES_DIR = ss.DATA_DIR, ss.SAVES_DIR
ctx4 = se.state_store.load_state("recon_user3", "reconcile_test")
ctx4["state"]["pacing"]["turn_count"] = 5
result = se.apply_thread_conditions(ctx4)
assert "s3" in result["activated"], result
assert ctx4["state"]["plot"]["subplots"]["s3"]["active"] is True
print("OK: a reconciled subplot is a real subplot - its activate_when fires exactly as it would for one seeded at save creation")

print("\nALL CHECKS PASSED: test_subplot_reconcile")
