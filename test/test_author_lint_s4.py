"""The linter rules Phase S4 added (Authoring_Tool_Spec §6): L02 fragment style, L05 gate stranding,
L11 an unresolved {var}, L12 a stat event in the rules that no axis prices, L14 the always-on
prompt budget and L15 an opening OPTIONS block that doesn't parse. (L03/L04 are `visibility`,
covered by test_visibility.py.)

Run directly: python3 test/test_author_lint_s4.py
"""
import copy
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _llm_stubs import load_story_engine  # noqa: E402

load_story_engine()
import author_lint  # noqa: E402

BASE = {
    "schema_version": 3, "meta": {"title": "T"}, "narration": {"pov": "second-person"},
    "world": {"setting_summary": "s", "rules": ["Be kind."],
              "locations": {"hall": {"name": "Hall", "description": "d", "connected_to": ["yard", "vault"]},
                            "yard": {"name": "Yard", "description": "d", "connected_to": ["hall"]},
                            "vault": {"name": "Vault", "description": "d", "connected_to": ["hall", "cellar"]},
                            "cellar": {"name": "Cellar", "description": "d", "connected_to": ["vault"]}}},
    "protagonist": {"default_name": "Traveller"},
    "plot": {"main_thread": {"title": "M", "description": "d", "acts": [{"act_number": 1, "title": "A", "description": "d"}]},
             "initial_scene": {"location": "hall", "summary": "s"},
             "opening_scene": {"narration_before_name": "Before.", "narration_after_name": "After."}},
    "mechanics": {},
}


def gates_of(*gates):
    raw = copy.deepcopy(BASE)
    raw["mechanics"]["gate"] = {"engine": "precondition", "gates": [
        {"id": f"g{i}", "target": t, "requires": {"flag": "f"}, "refusal_hint": hint} for i, (t, hint) in enumerate(gates)]}
    return raw

ids = lambda issues, rule: [i for i in issues if i["id"] == rule]  # noqa: E731

# --- L02: a fragment reads as a clause, not a finished sentence --------------------------------
def with_hint(text):
    raw = copy.deepcopy(BASE)
    raw["mechanics"]["endings"] = {"engine": "ending_funnel", "entries": [
        {"id": "e", "kind": "destination", "name": "E", "hint": text,
         "waypoints": [{"id": "w", "plant": text}]}]}
    return raw

assert author_lint.fragment_issues(with_hint("the ship is quieter with Lark aboard")) == []
got = author_lint.fragment_issues(with_hint("The ship is quieter with Lark aboard."))
assert len(got) == 2 and all(i["id"] == "L02" and i["severity"] == "warning" for i in got), got
assert "mechanics.endings.entries[0].hint" in got[0]["message"] and "capital" in got[0]["message"]
multi = author_lint.fragment_issues(with_hint("the ship is quiet. Lark hums to herself"))
assert multi and "more than one sentence" in multi[0]["message"], multi
assert [i["id"] for i in author_lint.fragment_issues(gates_of(("vault", "It is locked.")))] == ["L02"], \
    "a gate's refusal_hint is a fragment too"
print("OK: L02 flags a capitalised, full-stopped or multi-sentence fragment, and only fields the schema marks x-assist: fragment")

# --- L05: what a shut gate strands ---------------------------------------------------------------
gated = gates_of(("vault", "locked"))
got = author_lint.gate_issues(gated)
assert len(got) == 1 and got[0]["severity"] == "warning" and "cellar" in got[0]["message"] and "vault" in got[0]["message"], got
assert "yard" not in got[0]["message"], "a location reachable with every gate shut is not stranded"
assert author_lint.gate_issues(gates_of(("vault", "locked"), ("cellar", "locked"))) == [], "a gate target is not itself 'stranded'; nothing lies beyond the two"
got = author_lint.gate_issues(gates_of(("hall", "locked")))
assert [(i["id"], i["severity"]) for i in got] == [("L05", "error")], got
assert author_lint.gate_issues(BASE) == [] and author_lint.gate_issues(gates_of(("nowhere", "x"))) == [] and author_lint.gate_issues({"schema_version": 3}) == []
print("OK: L05 names what only a gate leads to (a warning), and errors on a gated opening location")

# --- L11: an unresolved {var} --------------------------------------------------------------------
raw = copy.deepcopy(BASE)
raw["world"]["setting_summary"] = "Lark is {lark_is}."
got = ids(author_lint.derived_issues(raw), "L11")
assert len(got) == 1 and "{lark_is}" in got[0]["message"], got
print("OK: an unresolved {var} is reported as L11")

# --- L12: a stat event in the rules that no axis prices ---------------------------------------------
raw = copy.deepcopy(BASE)
raw["mechanics"]["stats"] = {"engine": "bounded_counter", "axes": {"lattice": {"costs": {"lattice.rejoined": 5}}}}
raw["world"]["rules"] = ["Award lattice.rejoined when a node relights.", "Award lattice.relit for the same.",
                         "See e.g. the notes, or notes.txt.", "Nothing here."]
got = author_lint.stat_event_rule_issues(raw)
assert len(got) == 1 and "world.rules[1]" in got[0]["message"] and "lattice.relit" in got[0]["message"], got
raw["mechanics"].pop("stats")
assert author_lint.stat_event_rule_issues(raw) == [], "no priced events, nothing to compare against"
print("OK: L12 flags an unpriced event in a real namespace and leaves ordinary dotted prose alone")

# --- L14: the always-on prompt budget ---------------------------------------------------------------
raw = copy.deepcopy(BASE)
assert ids(author_lint.prompt_issues(raw), "L14") == []
raw["world"]["rules"] = ["A rule that goes on and on about how the world works. " * 20 for _ in range(12)]
got = ids(author_lint.prompt_issues(raw), "L14")
assert len(got) == 1 and got[0]["severity"] == "warning" and "world_rules" in got[0]["message"], got
print("OK: L14 warns when rules, style and the tracked entity outgrow the budget")

# --- L15: an opening OPTIONS block that doesn't parse -----------------------------------------------
raw = copy.deepcopy(BASE)
good = "You wake.\n\nOPTIONS:\n1. Look || You look around.\n2. Wait || You wait.\n3. Go || You go."
raw["plot"]["opening_scene"]["narration_after_name"] = good
assert ids(author_lint.prompt_issues(raw), "L15") == []
raw["plot"]["opening_scene"]["narration_after_name"] = "You wake.\n\nOPTIONS:\n1. Look\n2. Wait\n3. Go"
got = ids(author_lint.prompt_issues(raw), "L15")
assert len(got) == 1 and got[0]["severity"] == "error" and "0 of the 3" in got[0]["message"], got
raw["narration"]["option_count"] = 2
assert ids(author_lint.prompt_issues({**raw, "plot": {**raw["plot"], "opening_scene": {"narration_before_name": "b",
       "narration_after_name": good}}}), "L15") == [], "a story that asks for 2 options is not held to 3"
print("OK: L15 errors on an OPTIONS heading whose lines don't parse, and honours narration.option_count")

# --- narration.option_count is in the schema (the engine has always read it) -------------------------
narr = lambda raw: [e["message"] for e in author_lint.schema_errors(raw) if "narration" in e["message"]]  # noqa: E731
raw = copy.deepcopy(BASE)
raw["narration"]["option_count"] = 4
assert narr(raw) == [], narr(raw)
for bad in (1, 0, "4", 2.5):
    raw["narration"]["option_count"] = bad
    assert narr(raw), f"option_count {bad!r} should be refused"
print("OK: narration.option_count is an integer of at least 2")

# --- all of it runs through lint() ---------------------------------------------------------------------
raw = gates_of(("hall", "It is locked."))
found = {i["id"] for i in author_lint.lint(raw, author_lint.author_model.to_board_model(raw))}
assert {"L05", "L02"} <= found, found
print("OK: lint() runs the new rules")

print("\nALL CHECKS PASSED: test_author_lint_s4")
