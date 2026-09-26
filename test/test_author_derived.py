"""CR-04 creation-derived values (top-level `derived`) on the storyboard: `backend/derived.py`'s
rule, the board-model round trip, the loud refusal at load while nothing substitutes `{name}`,
and lint - every `{name}` a model would be handed must resolve for every way a player can finish
character creation.

Run directly: python3 test/test_author_derived.py
"""
import copy
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _llm_stubs  # noqa: E402,F401
import author_lint  # noqa: E402
import author_model  # noqa: E402
import conditions  # noqa: E402
import derived  # noqa: E402
import mechanics  # noqa: E402

GENDER = [("man", "a man"), ("woman", "a woman"), ("nonbinary", "nonbinary")]
TRADE = ["coil_hand", "listener", "patcher"]
RAW = {
    "schema_version": 3, "meta": {"title": "T"},
    "world": {"rules": ["Lark Ferris is {lark_is} ({lark_pron}), fixed for the whole story."]},
    "plot": {"opening_scene": {"narration_before_name": "Name?", "narration_after_name": "Hello, {player_name}."}},
    "character_creation": [
        {"key": "gender", "label": "Gender", "prompt": "p", "options": [{"id": g, "label": l} for g, l in GENDER]},
        {"key": "trade", "label": "Trade", "prompt": "p", "options": [{"id": t, "label": t} for t in TRADE]},
    ],
    # Story_Mechanics_Update.md CR-04's own example, first match wins.
    "derived": [
        {"when": {"creation": {"gender": "man"}}, "set": {"lark_pron": "she/her", "lark_is": "a woman"}},
        {"when": {"creation": {"gender": "woman"}}, "set": {"lark_pron": "he/him", "lark_is": "a man"}},
        {"when": {"creation": {"trade": "coil_hand"}}, "set": {"lark_pron": "he/him", "lark_is": "a man"}},
        {"when": {"all": []}, "set": {"lark_pron": "she/her", "lark_is": "a woman"}},
    ],
}

# --- the rule: the 3x3 gender x trade matrix the spec asks for ---------------------------------
rows = derived.table(RAW)
assert len(rows) == 9
expect = {("man", t): "she/her" for t in TRADE} | {("woman", t): "he/him" for t in TRADE} | {
    ("nonbinary", "coil_hand"): "he/him", ("nonbinary", "listener"): "she/her", ("nonbinary", "patcher"): "she/her"}
got = {(r["choices"]["gender"], r["choices"]["trade"]): r["values"]["lark_pron"] for r in rows}
assert got == expect, got
assert derived.combinations({"meta": {}}) == [{}], "no creation: exactly one way to start"
assert derived.variables(RAW) == ["lark_pron", "lark_is"]
assert sorted(n for _, n in derived.uses(RAW)) == ["lark_is", "lark_pron", "player_name"]
assert derived.builtin_for("plot.opening_scene.narration_after_name") == {"player_name"}
assert derived.builtin_for("world.rules[0]") == set()
big = {"character_creation": [{"key": f"s{i}", "options": [{"id": str(j)} for j in range(3)]} for i in range(7)]}
assert derived.combinations(big) is None, "3^7 = 2187 is past MAX_COMBINATIONS"
print("OK: first match wins across the 3x3 gender x trade matrix, exactly as CR-04 specifies")

# `when` is CLOSED: a typo'd option falls through rather than claiming the rule.
typo = copy.deepcopy(RAW)
typo["derived"][0]["when"] = {"creation": {"gender": "mann"}}
assert derived.resolve(typo, derived.creation_ctx(typo, {"gender": "man", "trade": "listener"}))[0] == 3
assert ("derived[0].when", conditions.CLOSED) in [(p, pol) for p, _, pol, _ in conditions.iter_conditions(RAW)]
assert any("unknown option 'mann'" in i["message"] for i in author_lint.condition_issues(typo))
print("OK: `when` is CLOSED - a typo falls through to the next rule, and lint names it (L10)")

# --- loud until the engine substitutes {name} --------------------------------------------------
try:
    mechanics.validate(RAW)
    raise AssertionError("a story authoring `derived` must not load for play yet")
except mechanics.UnknownEngineError as e:
    assert "derived" in str(e)
mechanics.validate({k: v for k, v in RAW.items() if k != "derived"})
print("OK: a story authoring `derived` is refused at load rather than handing the narrator {lark_is}")

# --- board model -------------------------------------------------------------------------------
m = author_model.to_board_model(RAW)
assert m["derived"] == RAW["derived"]
assert author_model.from_board_model(RAW, m)["derived"] == RAW["derived"]
bare = {"schema_version": 3, "meta": {"title": "T"}}
assert author_model.to_board_model(bare)["derived"] == []
assert "derived" not in author_model.from_board_model(bare, author_model.to_board_model(bare))
m = copy.deepcopy(m)
m["derived"][3].pop("when")
m["derived"][0]["set"][" "] = "dropped"
m["derived"][1]["set"]["lark_is"] = ""
out = author_model.from_board_model(RAW, m)["derived"]
assert out[3] == {"set": {"lark_pron": "she/her", "lark_is": "a woman"}}, "no condition means always"
assert " " not in out[0]["set"] and out[1]["set"]["lark_is"] == "", "a nameless row goes; an empty value is kept"
m["derived"] = []
assert "derived" not in author_model.from_board_model(RAW, m)
print("OK: derived round-trips untouched, is written back cleaned, and emptying removes it (P-2)")

# --- lint ------------------------------------------------------------------------------------
assert author_lint.derived_issues(RAW) == [], author_lint.derived_issues(RAW)
assert not [e for e in author_lint.schema_errors(RAW) if "derived" in e["message"]], author_lint.schema_errors(RAW)

def msgs(raw):
    return [(i["severity"], i["message"]) for i in author_lint.derived_issues(raw)]

x = copy.deepcopy(RAW)
x["world"]["rules"].append("The ship calls her {ship_name}.")
x["derived"].pop()                                        # no catch-all: nonbinary + listener matches nothing
x["derived"][1]["set"].pop("lark_pron")                   # woman: lark_pron unset
x["derived"].append({"when": {"creation": {"gender": "man"}}, "set": {"counter_value": "7"}})  # a built-in's name
got = msgs(x)
for sev, needle in (("error", "writes {ship_name}"), ("error", "No derived rule applies to 2 creation combinations"),
                    ("error", "gender = woman, trade = coil_hand gets rule 2, which doesn't set lark_pron"), ("error", "counter_value has the name of a placeholder"),
                    ("warning", "counter_value is set but no text writes"), ("warning", "Derived rule 4 never applies")):
    assert any(s == sev and needle in m for s, m in got), (needle, got)
y = copy.deepcopy(RAW)
y["derived"].insert(0, {"set": {"lark_pron": "they/them", "lark_is": "Lark"}})
assert any("Derived rule 2 never applies" in m for _, m in msgs(y)), msgs(y)
stat_rule = copy.deepcopy(y)
stat_rule["derived"][1]["when"] = {"stat": "grit", "gte": 5}
assert not any("rule 2 never" in m.lower() for _, m in msgs(stat_rule)), "a rule reading a stat isn't judged by creation alone"
print("OK: lint flags an unresolved {name}, uncovered combinations, a rule leaving a used name unset, "
      "a reserved name, unused values and unreachable rules")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for slug in ("example", os.path.join("private", "the_missing_core"), os.path.join("private", "new_babel")):
    path = os.path.join(REPO, "stories", slug, "template.json")
    if os.path.exists(path):
        raw = json.load(open(path, encoding="utf-8"))
        assert author_lint.derived_issues(raw) == [], (slug, author_lint.derived_issues(raw))
print("OK: the real stories' existing placeholders ({player_name}, pacing-directive fields, the stat readout) are not flagged")

print("\nALL CHECKS PASSED: test_author_derived")
