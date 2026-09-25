"""backend/author_evaluate.py: the board's sample-state evaluator (Authoring Tool decision D3,
Phase S2). Offline - the real `conditions.evaluate`, no Flask.

Run directly: python3 test/test_author_evaluate.py
"""
import copy
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _llm_stubs import load_story_engine  # noqa: E402

se = load_story_engine()
import author_evaluate  # noqa: E402
import author_model  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

STORY = {
    "protagonist": {"stats": {"sync": 20}},
    "mechanics": {
        "stats": {"engine": "bounded_counter", "floor": 0, "ceiling": 100, "axes": {"sync": {}, "reach": {}}},
        "revelations": {"engine": "triggered_reveal", "entries": [{"id": "frag_2", "trigger": "t", "content": "c"}]},
        "flags": {"declared": [{"id": "lark_departed", "detect": "Lark leaves"}]},
        "endings": {"engine": "ending_funnel", "entries": [{
            "id": "handover", "kind": "destination", "name": "The Handover",
            "viable_while": {"not": {"all": [{"stat": "sync", "gte": 85}, {"flag": "lark_departed"}]}},
            "ready_when": {"all": [{"stat": "reach", "gte": 60}, {"waypoints_done": "all"}]},
            "waypoints": [{"id": "aboard", "plant": "p", "done_when": {"flag": "lark_departed"}},
                          {"id": "reacts", "plant": "p", "detect": "d"}]}]},
    },
    "world": {"characters": {"Lark": {}}},
    "plot": {"subplots": {"sp_a": {"starts_active": True, "activate_when": {"revealed": "frag_2"}}}},
}


def rows(story, sample):
    """path -> row, with the unbuilt-engine list dropped."""
    return {x["path"]: x for x in author_evaluate.evaluate_all(story, sample)[0]}


before = copy.deepcopy(STORY)
sample = {"stats": {"sync": 85}, "flags": ["lark_departed"]}
r = rows(STORY, sample)
assert STORY == before, "evaluate_all must not mutate the story"

viable = r["mechanics.endings.entries[handover].viable_while"]
assert viable["satisfied"] is False and viable["polarity"] == "open", viable
print("OK: sample 'SYNC 85 and lark_departed set' shows viable_while failing (the ending is pruned)")

quiet = rows(STORY, {"stats": {"sync": 84}, "flags": ["lark_departed"]})
assert quiet["mechanics.endings.entries[handover].viable_while"]["satisfied"] is True
print("OK: SYNC 84 keeps it viable")

ready = r["mechanics.endings.entries[handover].ready_when"]
assert ready["satisfied"] is False and 0.0 < ready["proximity"] < 1.0, ready
full = rows(STORY, {"stats": {"reach": 60}, "waypoints_done": ["aboard", "reacts"]})
assert full["mechanics.endings.entries[handover].ready_when"]["satisfied"] is True
print("OK: ready_when is scoped to its own ending's waypoints and reports proximity")

wp = r["mechanics.endings.entries[handover].waypoints[aboard].done_when"]
assert wp["satisfied"] is True and wp["polarity"] == "closed"
act = r["plot.subplots.sp_a.activate_when"]
assert act["satisfied"] is False
assert rows(STORY, {"revealed": ["frag_2"]})["plot.subplots.sp_a.activate_when"]["satisfied"] is True
print("OK: waypoint done_when, activate_when and polarity are reported per field")

# Omitted keys keep the story's seeded value; hostile/garbage samples degrade, never raise.
seeded = author_evaluate.build_ctx(STORY, {})
assert seeded["state"]["protagonist"]["stats"] == {"sync": 20, "reach": 0}
for junk in (None, [], "x", {"stats": "no", "flags": None, "subplots": {"nope": "active"}, "turn": "9",
                              "relationships": {"Lark": True}}):
    author_evaluate.evaluate_all(STORY, junk)
print("OK: omitted keys keep the seeded value; a garbage sample degrades instead of raising")

# An unbuilt engine is reported, not swallowed: ending_funnel is not registered in this build,
# so the projection drops it, `left_out` names it, and a leaf that needs it reads unknown.
_, left_out = author_evaluate.evaluate_all(STORY, {})
assert ("endings", "ending_funnel") in left_out, left_out
print("OK: an unbuilt engine is reported in left_out")

# Real Missing Core, when its private submodule is checked out: every condition in the saved
# storyboard evaluates without error against an empty sample.
mc = os.path.join(REPO_ROOT, "stories", "private", "the_missing_core", "template.json")
if os.path.exists(mc):
    raw = json.load(open(mc, encoding="utf-8"))
    found, _ = author_evaluate.evaluate_all(raw, {})
    assert found and all(isinstance(x["label"], str) for x in found)
    print(f"OK: the saved Missing Core storyboard evaluates - {len(found)} conditions")
else:
    print("SKIPPED (real story): stories/private/the_missing_core is not checked out")


# --- stat_tiers: the S3 sidebar's readout, through the real BoundedCounter.tier_for ------------
TIERED = copy.deepcopy(STORY)
TIERED["mechanics"]["stats"]["axes"]["sync"] = {"tiers": [{"at": 0, "label": "low"}, {"at": 50, "label": "high"}]}
tiers = author_evaluate.stat_tiers(TIERED, {"stats": {"sync": 50}})
assert tiers["sync"] == {"value": 50, "tier": "high"}, tiers
assert tiers["reach"] == {"value": 0, "tier": None}, tiers
assert author_evaluate.stat_tiers(TIERED, {})["sync"] == {"value": 20, "tier": "low"}, "seeded value when unsampled"
unbound = copy.deepcopy(TIERED)
del unbound["mechanics"]["stats"]["engine"]
assert author_evaluate.stat_tiers(unbound, {"stats": {"sync": 50}}) == {}, "no stats engine, no tier line"
print("OK: stat_tiers reports each axis's sample value and engine-resolved tier")

print("\nALL CHECKS PASSED: test_author_evaluate")
