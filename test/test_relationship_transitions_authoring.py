"""CR-08 relationship tier transitions, authoring side: `mechanics.relationships.transitions` in
the schema, round-tripping through the board's Forms tab untouched, lint (duplicate ids, an
inverted between, an unknown tier, a directive that never says {name}, a flag nothing declares, two
characters sharing a first name), the derived-variable scanner accepting {name}/{id} there, and a
load warning while no engine evaluates them.

Run directly: python3 test/test_relationship_transitions_authoring.py
"""
import contextlib
import copy
import io
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _llm_stubs  # noqa: E402,F401
import author_lint  # noqa: E402
import author_model  # noqa: E402
import derived  # noqa: E402
import mechanics  # noqa: E402

TRANSITION = {"id": "drifting_out", "when": {"peak_gte": 25, "between": [-10, 10]}, "once_per_character": True,
              "directive": "{name} is leaving this story: give them an exit that costs something.",
              "sets_flag": "{id}_departed"}
RAW = {"schema_version": 3, "meta": {"title": "T"},
       "world": {"setting_summary": "s", "characters": {"Lark Ferris": {"name": "Lark Ferris", "description": "d"},
                                                       "Oren Thale": {"name": "Oren Thale", "description": "d"}}},
       "mechanics": {"relationships": {"engine": "scored_axis", "registers": {"kind": 3},
                                       "tiers": [{"at": 25, "label": "warm"}, {"at": -25, "label": "guarded"}],
                                       "transitions": [TRANSITION]},
                     "flags": {"declared": [{"id": "lark_departed", "detect": "d"},
                                            {"id": "oren_departed", "detect": "d"}]}}}

rel_errors = lambda raw: [e["message"] for e in author_lint.schema_errors(raw) if "relationships" in e["message"]]  # noqa: E731
assert rel_errors(RAW) == [], rel_errors(RAW)
bad = copy.deepcopy(RAW)
bad["mechanics"]["relationships"]["transitions"] = [{"id": "x", "when": {}, "directive": "d"},
                                                    {"id": "y", "when": {"between": [1]}, "directive": "d"},
                                                    {"when": {"gte": 1}, "directive": "d"}]
assert len(rel_errors(bad)) >= 3, rel_errors(bad)
print("OK: transitions are schema-checked (a when needs a modifier, between needs two numbers, id and directive required)")

model = author_model.to_board_model(RAW)
assert author_model.from_board_model(RAW, model)["mechanics"]["relationships"] == RAW["mechanics"]["relationships"]
print("OK: they round-trip through the board untouched")

assert author_lint.transition_issues(RAW) == [], author_lint.transition_issues(RAW)
assert [i for i in author_lint.lint(RAW, model) if i.get("id") == "transitions"] == []
x = copy.deepcopy(RAW)
x["mechanics"]["relationships"]["transitions"] = [
    {**TRANSITION, "id": "a", "when": {"between": [10, -10], "tier_gte": "devoted"}, "directive": "They leave."},
    {**TRANSITION, "id": "a"}]
del x["mechanics"]["flags"]["declared"][1]
got = [(i["severity"], i["message"]) for i in author_lint.transition_issues(x)]
has = lambda sev, text: any(s == sev and text in m for s, m in got)  # noqa: E731
assert has("error", "authored twice"), got
assert has("error", "never hold"), got
assert has("error", "devoted"), got
assert has("warning", "never says {name}"), got
assert has("warning", "Oren Thale the resulting flag is not declared"), got
share = copy.deepcopy(RAW)
share["world"]["characters"]["Lark Vane"] = {"name": "Lark Vane", "description": "d"}
assert any(s == "error" and "share lark" in m for s, m in
           [(i["severity"], i["message"]) for i in author_lint.transition_issues(share)])
plain = copy.deepcopy(RAW)
plain["mechanics"]["relationships"]["transitions"][0]["sets_flag"] = "someone_left"
assert any("not a declared flag" in i["message"] for i in author_lint.transition_issues(plain))
print("OK: lint catches duplicates, inverted between, unknown tiers, silent directives, undeclared and shared flags")

assert all(name in derived.builtin_for(p) for p, name in derived.uses(RAW) if p.startswith("mechanics.relationships.transitions"))
assert {"name", "id"} <= derived.RESERVED
print("OK: {name} and {id} are engine-filled placeholders there, so a derived value can't take them")

buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    mechanics.validate(RAW)
assert "CR-08" not in buf.getvalue(), "transitions are read now: no warning"
quiet = copy.deepcopy(RAW)
del quiet["mechanics"]["relationships"]["transitions"]
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    mechanics.validate(quiet)
assert "CR-08" not in buf.getvalue()
print("OK: authored transitions warn at load while no engine evaluates them; a story without any stays quiet")

print("\nALL CHECKS PASSED: test_relationship_transitions_authoring")
