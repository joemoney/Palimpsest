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
