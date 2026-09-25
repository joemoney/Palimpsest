"""backend/author_lint.py: S1's server-side lint subset (L01/L08/L09/L16, structural-flow,
Cast checks - AUTHORING_TOOL_PHASES.md Phase S1, decision D3).

Plain-script style matching test_author_model.py: module-level assertions, run directly or
via test/run_all.py. Synthetic `{nodes, edges, characters}` models exercise each rule in
isolation; the real stories/fixtures are exercised at the bottom as a no-crash smoke test
with pinned issue counts, so a rule change here has to update this file rather than silently
drifting.

Run directly: python3 test/test_author_lint.py
"""
import glob
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))
import author_lint  # noqa: E402
import author_model  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# L01 needs Draft 2020-12 (jsonschema>=4.18 - CLAUDE.md Phase 0). This repo's own local
# environment has 3.2.0, which lacks Draft202012Validator entirely - skip gracefully rather
# than crash, the same contract test_app_routes.py uses for a missing flask.
import jsonschema  # noqa: E402
if not hasattr(jsonschema, "Draft202012Validator"):
    print(f"SKIPPED: jsonschema {jsonschema.__version__} has no Draft202012Validator - "
          "needs jsonschema>=4.18 (`pip install -r requirements.txt`) to run for real.")
    sys.exit(0)


def ids(issues):
    return [i["id"] for i in issues]


def by_id(issues, rule_id):
    return [i for i in issues if i["id"] == rule_id]


# --- L08: no catch-all destination -------------------------------------------------------
model = {"nodes": [
    {"id": "start", "kind": "start"},
    {"id": "end1", "kind": "ending", "ekind": "destination", "title": "End One",
     "catchAll": False, "waypoints": []},
], "edges": [], "characters": []}
assert "L08" in ids(author_lint.structural_issues(model))

model_ok = {"nodes": [
    {"id": "start", "kind": "start"},
    {"id": "end1", "kind": "ending", "ekind": "destination", "title": "End One",
     "catchAll": True, "waypoints": []},
], "edges": [], "characters": []}
assert "L08" not in ids(author_lint.structural_issues(model_ok))

# --- L09: waypoint with neither done_when nor detect --------------------------------------
model = {"nodes": [
    {"id": "start", "kind": "start"},
    {"id": "sp1", "kind": "subplot", "title": "Thread", "role": "spine", "starts_active": True},
    {"id": "end1", "kind": "ending", "ekind": "destination", "title": "End", "catchAll": True,
     "waypoints": [{"id": "w1", "plant": "a clue appears"}]},
], "edges": [
    {"type": "opens", "from": "start", "to": "sp1"},
    {"type": "delivers", "from": "sp1", "to": "end1", "wp": "w1"},
], "characters": []}
issues = author_lint.structural_issues(model)
assert len(by_id(issues, "L09")) == 1, issues

model_ok = json.loads(json.dumps(model))
model_ok["nodes"][2]["waypoints"][0]["done_when"] = {"flag": "clue_found"}
assert not by_id(author_lint.structural_issues(model_ok), "L09")

model_ok2 = json.loads(json.dumps(model))
model_ok2["nodes"][2]["waypoints"][0]["detect"] = "the clue is mentioned"
assert not by_id(author_lint.structural_issues(model_ok2), "L09")

# --- L16: dangling edge ids ----------------------------------------------------------------
model = {"nodes": [{"id": "start", "kind": "start"}],
         "edges": [{"type": "opens", "from": "start", "to": "does_not_exist"}],
         "characters": []}
assert len(by_id(author_lint.structural_issues(model), "L16")) == 1

# --- structural: uncarried waypoint ---------------------------------------------------------
model = {"nodes": [
    {"id": "start", "kind": "start"},
    {"id": "sp1", "kind": "subplot", "title": "Thread", "role": "spine", "starts_active": True},
    {"id": "end1", "kind": "ending", "ekind": "destination", "title": "End", "catchAll": True,
     "waypoints": [{"id": "w1", "plant": "x", "done_when": {"flag": "f"}},
                   {"id": "w2", "plant": "y", "done_when": {"flag": "g"}}]},
], "edges": [
    {"type": "opens", "from": "start", "to": "sp1"},
    {"type": "delivers", "from": "sp1", "to": "end1", "wp": "w1"},
], "characters": []}
issues = author_lint.structural_issues(model)
uncarried = [i for i in issues if i["severity"] == "warning" and "no thread to carry" in i["message"]]
assert len(uncarried) == 1, issues

# --- structural: single-carrier destination -------------------------------------------------
model = {"nodes": [
    {"id": "start", "kind": "start"},
    {"id": "sp1", "kind": "subplot", "title": "Only Thread", "role": "spine", "starts_active": True},
    {"id": "end1", "kind": "ending", "ekind": "destination", "title": "End", "catchAll": False,
     "waypoints": [{"id": "w1", "plant": "x", "done_when": {"flag": "f"}}]},
    {"id": "end2", "kind": "ending", "ekind": "destination", "title": "Catchall", "catchAll": True,
     "waypoints": []},
], "edges": [
    {"type": "opens", "from": "start", "to": "sp1"},
    {"type": "delivers", "from": "sp1", "to": "end1", "wp": "w1"},
], "characters": []}
single_carrier = [i for i in author_lint.structural_issues(model) if "rests on one thread" in i["message"]]
assert len(single_carrier) == 1, single_carrier

# --- structural: thread never activates -----------------------------------------------------
model = {"nodes": [
    {"id": "start", "kind": "start"},
    {"id": "sp1", "kind": "subplot", "title": "Orphan Thread"},
], "edges": [], "characters": []}
issues = author_lint.structural_issues(model)
never = [i for i in issues if "never becomes active" in i["message"]]
assert len(never) == 1, issues

# --- structural: an unlock link with no condition is an error, not a silent TODO -------------
model = {"nodes": [
    {"id": "start", "kind": "start"},
    {"id": "sp1", "kind": "subplot", "title": "Gated", "role": "spine"},
], "edges": [{"type": "unlocks", "from": "start", "to": "sp1"}], "characters": []}
assert any("unlock link with no condition" in i["message"]
           for i in author_lint.structural_issues(model))
model["edges"][0]["cond_raw"] = {"stat": {"axis": "reach", "at_least": 20}}
assert not any("unlock link with no condition" in i["message"]
               for i in author_lint.structural_issues(model))

# --- structural: spine carries nothing / texture carries something --------------------------
model = {"nodes": [
    {"id": "start", "kind": "start"},
    {"id": "sp1", "kind": "subplot", "title": "Spine", "role": "spine", "starts_active": True},
    {"id": "sp2", "kind": "subplot", "title": "Texture", "role": "texture", "starts_active": True},
    {"id": "end1", "kind": "ending", "ekind": "destination", "title": "End", "catchAll": True,
     "waypoints": [{"id": "w1", "plant": "x", "done_when": {}}]},
], "edges": [
    {"type": "opens", "from": "start", "to": "sp1"},
    {"type": "opens", "from": "start", "to": "sp2"},
    {"type": "delivers", "from": "sp2", "to": "end1", "wp": "w1"},
], "characters": []}
issues = author_lint.structural_issues(model)
assert any("Spine is a spine thread but carries no waypoint" in i["message"] for i in issues), issues
assert any("Texture is texture but carries waypoints" in i["message"] for i in issues), issues

# --- structural: delivery with no waypoint chosen --------------------------------------------
model = {"nodes": [
    {"id": "start", "kind": "start"},
    {"id": "sp1", "kind": "subplot", "title": "Thread", "starts_active": True},
    {"id": "end1", "kind": "ending", "ekind": "destination", "title": "End", "catchAll": True, "waypoints": []},
], "edges": [
    {"type": "opens", "from": "start", "to": "sp1"},
    {"type": "delivers", "from": "sp1", "to": "end1"},
], "characters": []}
issues = author_lint.structural_issues(model)
assert any("choose which waypoint" in i["message"] for i in issues), issues

# --- cast: no name / duplicate name / no description / canon leak ---------------------------
model = {"nodes": [], "edges": [], "characters": [
    {"name": "", "description": "someone"},
    {"name": "Alex", "description": "Alex"},
    {"name": "Alex", "description": "Alex again"},
    {"name": "Bo"},
]}
issues = author_lint.cast_issues(model)
assert any("has no name" in i["message"] for i in issues), issues
assert any("overwrites the first" in i["message"] for i in issues), issues
assert any("has no description" in i["message"] and i.get("char") == "Bo" for i in issues), issues

long_canon = "the operator has been quietly siphoning power from the reactor for eleven days"
model = {"nodes": [], "edges": [], "characters": [
    {"name": "Rael", "description": f"Everyone in Millbrook says {long_canon} and no one stops him.",
     "canon": [{"k": "secret", "v": long_canon}]},
]}
issues = author_lint.cast_issues(model)
assert any("repeats canon" in i["message"] for i in issues), issues

no_leak_model = {"nodes": [], "edges": [], "characters": [
    {"name": "Rael", "description": "A quiet man who keeps to himself.",
     "canon": [{"k": "secret", "v": long_canon}]},
]}
assert not any("repeats canon" in i["message"] for i in author_lint.cast_issues(no_leak_model))

# --- L01: schema errors surface with a path ---------------------------------------------------
bad_raw = {"schema_version": 3, "story_version": "x", "meta": {"title": "T"},
           "narration": {"pov": "second-person"}, "world": {"setting_summary": "s", "rules": []},
           "protagonist": {"default_name": "P"}, "mechanics": {}, "plot": {}, "not_a_real_key": True}
errors = author_lint.schema_errors(bad_raw)
assert errors, "expected schema errors for an unknown top-level key"
assert any("not_a_real_key" in e["message"] for e in errors), errors

# --- L01: the condition grammar is CR-02's canonical spelling, not free text -----------------
import jsonschema as _js  # noqa: E402
_schema = json.load(open(os.path.join(REPO_ROOT, "schema", "template.v3.schema.json"), encoding="utf-8"))
_cond = _js.Draft202012Validator({"$ref": "#/$defs/condition", "$defs": _schema["$defs"]})
GOOD = [
    {"stat": "reach", "gte": 50},
    {"stat": "reach", "between": [10, 20]},
    {"revealed": "frag_0002"},
    {"relationship": "Lark Ferris", "tier_gte": "warm"},
    {"subplot_status": {"subplot_003": "progressed"}},
    {"waypoints_done": "all"},
    {"stat": {"axis": "reach", "at_least": 50}},  # pre-overhaul gate form: CR-02 rewrites, still valid
    {"revelation": "frag_0001"},
    {"all": [{"stat": "sync", "gte": 70}, {"revealed": "frag_0002"},
             {"any": [{"relationship": "Lark Ferris", "tier_gte": "warm"}, {"flag": "lark_aboard"}]},
             {"not": {"tier_reached": ["trace", "loud"]}}]},  # CR-02's own worked example
]
BAD = [
    {"condition": "REACH >= 50"},          # what the free-text box used to write
    {"condition": "TODO"},
    {"stat": "reach"},                     # a stat leaf needs a comparator
    {"relationship": "Lark Ferris"},       # ... and so does a relationship leaf
    {"any": [{"condition": "TODO"}]},
    {"stat": "reach", "gtee": 50},         # typo'd key
]
for c in GOOD:
    assert _cond.is_valid(c), c
for c in BAD:
    assert not _cond.is_valid(c), c
print("OK: conditions validate as CR-02 canonical grammar; free text and half-built leaves are L01")

# --- L10: every condition field names only what the story defines ------------------------------
import copy  # noqa: E402
_base = json.load(open(os.path.join(REPO_ROOT, "stories", "example", "template.json"), encoding="utf-8"))
_base["schema_version"] = author_model.TEMPLATE_SCHEMA_VERSION
_sid = next(iter(_base["plot"]["subplots"]))


def _l10(mutate):
    raw = copy.deepcopy(_base)
    mutate(raw)
    return by_id(author_lint.lint(raw, author_model.to_board_model(raw)), "L10")


assert not _l10(lambda r: None), "the untouched example story has no L10 finding"
hit = _l10(lambda r: r["plot"]["subplots"][_sid].update(activate_when={"flag": "typo"}))
assert len(hit) == 1 and "typo" in hit[0]["message"] and hit[0]["severity"] == "error", hit
assert f"plot.subplots.{_sid}.activate_when" in hit[0]["message"], hit
print("OK: L10 - an undeclared flag in a condition is a save-blocking error naming the field")


def _declare(r):
    r["mechanics"]["flags"] = {"declared": [{"id": "typo", "detect": "the typo happens"}]}
    r["plot"]["subplots"][_sid]["activate_when"] = {"flag": "typo"}


assert not _l10(_declare), "declaring the flag clears it"
print("OK: L10 - mechanics.flags.declared makes the flag a legitimate referent")

for field, cond in [("fail_when", {"stat": "no_such_axis", "gte": 1}),
                    ("activate_when", {"revealed": "no_such_frag"}),
                    ("fail_when", {"relationship": "No One", "tier_gte": "warm"})]:
    assert _l10(lambda r, f=field, c=cond: r["plot"]["subplots"][_sid].update({f: c})), (field, cond)


def _gate_and_ending(r):
    r["mechanics"]["endings"] = {"engine": "ending_funnel", "entries": [
        {"id": "e1", "kind": "destination", "name": "E", "ready_when": {"flag": "nope"},
         "waypoints": [{"id": "w", "plant": "p", "done_when": {"stat": "ghost", "gte": 1}}]}]}


found = _l10(_gate_and_ending)
assert {("ready_when" in i["message"]) for i in found} == {True, False}, found
assert any("done_when" in i["message"] for i in found), found
print("OK: L10 covers gates, activate_when/fail_when, and every ending condition field")

# --- declared-flag hygiene ---------------------------------------------------------------------
def _flag_issues(declared):
    raw = copy.deepcopy(_base)
    raw["mechanics"]["flags"] = {"declared": declared}
    return by_id(author_lint.lint(raw, author_model.to_board_model(raw)), "flags")


assert not _flag_issues([{"id": "a", "detect": "a happens"}])
assert [i["severity"] for i in _flag_issues([{"id": "a"}])] == ["warning"]
dup = _flag_issues([{"id": "a", "detect": "x"}, {"id": "a", "detect": "y"}])
assert [i["severity"] for i in dup] == ["error"], dup
print("OK: a declared flag with no detect warns; a duplicate id is an error")

# --- L06/L07: stat tier ladders (S3) ------------------------------------------------------------
def _tier_issues(axes, **stats):
    raw = {"protagonist": {"stats": {"grit": 1}},
           "mechanics": {"stats": {"engine": "bounded_counter", "floor": 0, **stats, "axes": axes}}}
    return author_lint.stat_tier_issues(raw)


assert not _tier_issues({"grit": {"tiers": [{"at": 0}, {"at": 10}]}})
none = _tier_issues({})
assert [(i["id"], i["severity"], i["axis"]) for i in none] == [("L06", "warning", "grit")], none
high = _tier_issues({"grit": {"tiers": [{"at": 5}, {"at": 10}]}})
assert [(i["id"], i["severity"]) for i in high] == [("L06", "warning")] and "floor" in high[0]["message"], high
# The floor is the axis's own when it overrides the block's.
assert not _tier_issues({"grit": {"floor": -10, "tiers": [{"at": -10}]}})
unsorted = _tier_issues({"grit": {"tiers": [{"at": 0}, {"at": 20}, {"at": 10}]}})
assert [(i["id"], i["severity"]) for i in unsorted] == [("L07", "error")], unsorted
dupe = _tier_issues({"grit": {"tiers": [{"at": 0}, {"at": 10}, {"at": 10}]}})
assert [(i["id"], i["severity"]) for i in dupe] == [("L07", "error")] and "10" in dupe[0]["message"], dupe
# No bound stats engine: nothing to tier, nothing to say (the engine warning covers it).
assert not author_lint.stat_tier_issues({"protagonist": {"stats": {"grit": 1}}, "mechanics": {"stats": {}}})
assert not author_lint.stat_tier_issues({"meta": {}})
print("OK: L06 warns on a tierless axis or a ladder above its floor; L07 errors on disorder and duplicates")

# --- has_errors -------------------------------------------------------------------------------
assert author_lint.has_errors([{"severity": "error"}])
assert not author_lint.has_errors([{"severity": "warning"}])
assert not author_lint.has_errors([])

# --- real stories and fixtures: no crash, pinned counts (catches unnoticed rule drift) --------
REAL_FILES = {
    "example": (os.path.join(REPO_ROOT, "stories", "example", "template.json"), 2),
    "regency": (os.path.join(REPO_ROOT, "test", "fixtures", "regency.json"), 1),
    "courtroom": (os.path.join(REPO_ROOT, "test", "fixtures", "courtroom.json"), 1),
    "survival": (os.path.join(REPO_ROOT, "test", "fixtures", "survival.json"), 1),
}
for label, (path, expected_errors) in REAL_FILES.items():
    raw = json.load(open(path, encoding="utf-8"))
    raw["schema_version"] = author_model.TEMPLATE_SCHEMA_VERSION
    model = author_model.to_board_model(raw)
    issues = author_lint.lint(raw, model)
    actual_errors = sum(1 for i in issues if i["severity"] == "error")
    assert actual_errors == expected_errors, (label, actual_errors, issues)
    # None of these 4 files author mechanics.endings/CR-10 content yet (that's Phase S1 step 8,
    # not done) - so every error here must be the universal "no catch-all" (L08), never L01.
    assert not by_id(issues, "L01"), (label, by_id(issues, "L01"))

assert len(glob.glob(os.path.join(REPO_ROOT, "test", "fixtures", "*.json"))) == 3

print(f"test_author_lint.py: all checks passed.")
