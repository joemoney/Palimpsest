"""CR-11 on the storyboard: `mechanics.bonds` (the Cast tab's bond grid) and
`mechanics.side_threads` (the Side threads tab) through the board model and back, the `bond`
condition leaf, recipe conditions that name cast slots, and their lint. Neither engine is built
yet (build order: the storyboard leads), so this covers authoring only.

Run directly: python3 test/test_author_cr11.py
"""
import copy
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _llm_stubs  # noqa: E402,F401
import author_evaluate  # noqa: E402
import author_lint  # noqa: E402
import author_model  # noqa: E402
import conditions  # noqa: E402

MIRA, SAL = "Mira Venn", "Salome Vence"
RAW = {
    "schema_version": 3,
    "meta": {"title": "T"},
    "world": {"characters": {MIRA: {"name": MIRA, "description": "an auditor"},
                             SAL: {"name": SAL, "description": "an advocate"},
                             "Lark": {"name": "Lark", "description": "a diver"}},
              "locations": {"loc_tally": {"name": "Tally", "description": "d"}}},
    "mechanics": {
        "pacing_loop": {"engine": "beat_counter", "beats": {"threat": {}, "respite": {}}},
        "inventory": {"engine": "tagged_items", "tags": ["document", "tool"]},
        "bonds": {"engine": "scored_bonds",
                  "registers": {"covered_for_them": 8, "betrayed_them": -15},
                  "tiers": [{"at": 25, "label": "warm"}, {"at": -25, "label": "wary"}],
                  "seed": [{"from": MIRA, "to": SAL, "score": -20}]},
        "side_threads": {"engine": "episodic_threads", "max_active": 2, "start_after_beats": ["respite"],
                         "protected": ["Lark"],
                         "recipes": [{"id": "unrequited", "cast": {"a": {"from": "any"}, "b": {"from": "any"}},
                                      "eligible_when": {"all": [{"bond": ["a", "b"], "tier_gte": "warm"},
                                                                {"bond": ["b", "a"], "lte": 0}]},
                                      "premise": "one of them has started to care",
                                      "may_move": ["bond:a,b", "bond:b,a", "relationship:a"]}]},
    },
}

# --- board model round trip -------------------------------------------------------------------
model = author_model.to_board_model(RAW)
assert model["bonds"] == RAW["mechanics"]["bonds"] and model["side_threads"] == RAW["mechanics"]["side_threads"]
assert author_model.from_board_model(RAW, model)["mechanics"] == RAW["mechanics"]
bare = {"schema_version": 3, "meta": {"title": "T"}}
m = author_model.to_board_model(bare)
assert m["bonds"] is None and m["side_threads"] is None
assert "mechanics" not in author_model.from_board_model(bare, m), "an untouched board adds no block (P-2)"
print("OK: both blocks load as the template holds them and round-trip untouched; absent stays absent")

m = copy.deepcopy(model)
m["bonds"]["seed"].append({"from": SAL, "to": MIRA, "score": 0})
m["bonds"]["axis"] = {"negative": "", "positive": "devoted"}
m["side_threads"]["recipes"][0]["premise"] = "  "
m["side_threads"]["default_recipe"] = False
out = author_model.from_board_model(RAW, m)["mechanics"]
assert out["bonds"]["seed"][-1] == {"from": SAL, "to": MIRA, "score": 0}, "a seed of 0 is a real value"
assert out["bonds"]["axis"] == {"positive": "devoted"}, "a cleared field is absent, not blank"
assert "premise" not in out["side_threads"]["recipes"][0]
assert out["side_threads"]["default_recipe"] is False, "false is a real choice"
assert list(out["bonds"])[0] == "engine" and out["bonds"]["engine"] == "scored_bonds"
m["bonds"] = None
assert "bonds" not in author_model.from_board_model(RAW, m)["mechanics"]
m = author_model.to_board_model(bare)
m["side_threads"] = {}
assert author_model.from_board_model(bare, m)["mechanics"]["side_threads"] == {"engine": "episodic_threads"}
print("OK: edits prune blanks (keeping 0 and false), a new block declares its engine, None removes it")

# --- the bond leaf ------------------------------------------------------------------------------
def ctx(bonds=None, story=RAW):
    state = {"mechanics": {"bonds": bonds}} if bonds is not None else {}
    return {"story": story, "state": state}

C, O = conditions.CLOSED, conditions.OPEN
assert conditions.satisfied({"bond": [SAL, MIRA], "gte": 0}, ctx(), C), "an unopened bond reads 0, not unknown"
assert not conditions.satisfied({"bond": [SAL, MIRA], "gte": 1}, ctx(), O), "... under either polarity"
seeded = {MIRA: {SAL: {"score": -20}}, SAL: {MIRA: {"score": 28}}}
assert conditions.satisfied({"bond": [SAL, MIRA], "tier_gte": "warm"}, ctx(seeded), C)
assert not conditions.satisfied({"bond": [MIRA, SAL], "tier_gte": "warm"}, ctx(seeded), O), "bonds are one-way"
assert not conditions.satisfied({"bond": [MIRA, SAL], "tier_lte": "wary"}, ctx(seeded), C), "-20 is above wary (-25)"
assert conditions.satisfied({"bond": [MIRA, SAL], "tier_lte": "wary"}, ctx({MIRA: {SAL: {"score": -30}}}), C)
r = conditions.evaluate({"bond": ["Nobody", SAL], "gte": 0}, ctx(), C)
assert not r.satisfied and r.unknown, r
assert conditions.satisfied({"bond": [MIRA, SAL], "tier_gte": "nope"}, ctx(), O), "an unknown tier fails open under OPEN"
no_bonds = {k: v for k, v in RAW.items() if k != "mechanics"}
assert conditions.evaluate({"bond": [MIRA, SAL], "gte": 0}, ctx(story=no_bonds), C).unknown
assert conditions.describe({"bond": [SAL, MIRA], "tier_gte": "warm"}) == f"{SAL} → {MIRA} at least warm"
bound = conditions.bind_slots(RAW["mechanics"]["side_threads"]["recipes"][0]["eligible_when"], {"a": SAL, "b": MIRA})
assert bound == {"all": [{"bond": [SAL, MIRA], "tier_gte": "warm"}, {"bond": [MIRA, SAL], "lte": 0}]}
assert conditions.satisfied(bound, ctx(seeded), C)
print("OK: bond reads directionally, 0 when unopened, tiers from mechanics.bonds; bind_slots casts a recipe")

# --- recipe conditions: CLOSED, slots are legal names there and only there -----------------------
rows = [(p, pol, scope) for p, _, pol, scope in conditions.iter_conditions(RAW) if "side_threads" in p]
assert [(p, pol) for p, pol, _ in rows] == [("mechanics.side_threads.recipes[unrequited].eligible_when", C)]
assert author_lint.condition_issues(RAW) == []
assert conditions.check({"bond": ["a", "b"], "gte": 1}, RAW) == [
    "names unknown character 'a'", "names unknown character 'b'"], "outside a recipe a slot is nobody"
bad = copy.deepcopy(RAW)
bad["mechanics"]["side_threads"]["recipes"][0]["eligible_when"] = {"bond": ["a", "c"], "tier_gte": "cosy"}
msgs = [i["message"] for i in author_lint.condition_issues(bad)]
assert any("'c'" in x for x in msgs) and any("'cosy'" in x for x in msgs), msgs
assert conditions.check({"bond": [MIRA, MIRA], "gte": 1}, RAW) == [f"bond {MIRA!r} -> {MIRA!r} is a character's bond with themselves"]
print("OK: recipe conditions are CLOSED and may name cast slots; anywhere else a slot is an unknown character")

# --- lint -------------------------------------------------------------------------------------
assert author_lint.bond_issues(RAW) == [] and author_lint.side_thread_issues(RAW) == [], \
    (author_lint.bond_issues(RAW), author_lint.side_thread_issues(RAW))
b = copy.deepcopy(RAW)
b["mechanics"]["bonds"]["registers"] = {}
b["mechanics"]["bonds"]["tiers"].append({"at": 60, "label": "warm"})
b["mechanics"]["bonds"]["seed"] += [{"from": "Ghost", "to": SAL, "score": 1}, {"from": MIRA, "to": SAL, "score": 3}]
got = [(i["id"], i["message"]) for i in author_lint.bond_issues(b)]
assert any("no registers" in m for _, m in got) and any("warm is used twice" in m for _, m in got)
assert ("L10", f"Bond seed Ghost → {SAL} names Ghost, who is not a character in this story.") in got
assert any("authored twice" in m for _, m in got), got
s = copy.deepcopy(RAW)
st = s["mechanics"]["side_threads"]
st["start_after_beats"] = ["lull"]
st["protected"].append("Ghost")
st["recipes"].append({"id": "papers", "premise": "p",
                      "cast": {"a": {"from": "Lark"}, "place": {"kind": "location", "id": "loc_gone"},
                               "doc": {"kind": "item", "tag": "salvage"}, "old": {"from": "followed"}},
                      "may_move": ["stat:trace", "leverage:access", "bond:a,place"],
                      "follows": {"recipe": "nothing"}})
msgs = [i["message"] for i in author_lint.side_thread_issues(s)]
for needle in ("beat lull", "protect Ghost", "names Lark, who is protected", "location loc_gone",
               "item tag salvage", "doesn't say which of its slots", "stat trace", "no mechanics.progression",
               "place, which is not a character slot", "follows recipe nothing"):
    assert any(needle in x for x in msgs), (needle, msgs)
nb = copy.deepcopy(RAW)
del nb["mechanics"]["bonds"]
assert any("built-in recipe casts the pair" in i["message"] for i in author_lint.side_thread_issues(nb))
np_ = copy.deepcopy(RAW)
del np_["mechanics"]["pacing_loop"]
assert any("no mechanics.pacing_loop" in i["message"] for i in author_lint.side_thread_issues(np_))
schema_bad = copy.deepcopy(RAW)
schema_bad["mechanics"]["bonds"]["tiers"][0]["narration"] = "they glow"
assert any("bonds" in e["message"] for e in author_lint.schema_errors(schema_bad)), \
    "a bond tier carries a label only (CR-11 decision 2)"
assert not [e for e in author_lint.schema_errors(RAW) if "bonds" in e["message"] or "side_threads" in e["message"]]
print("OK: lint catches dangling names, protected casting, unknown beats/tags/stats/recipes, and inert blocks")

# --- the sample state answers bond conditions while the engine is unbuilt ------------------------
story = copy.deepcopy(RAW)
story["plot"] = {"main_thread": {"title": "m", "description": "d", "acts": [
    {"act_number": 1, "title": "A", "description": "d", "requires": {"bond": [MIRA, SAL], "gte": 0}},
    {"act_number": 2, "title": "B", "description": "d", "requires": {"turn_gte": 3}}]}}
rows, left_out = author_evaluate.evaluate_all(story, {"bonds": {MIRA: {SAL: 5}}, "turn": 4})
by = {r["path"]: r for r in rows}
assert ("bonds", "scored_bonds") in left_out and ("side_threads", "episodic_threads") in left_out
assert by["plot.main_thread.acts[0].requires"]["satisfied"] and not by["plot.main_thread.acts[0].requires"]["unknown"]
assert by["plot.main_thread.acts[1].requires"]["satisfied"], "other leaves still evaluate"
assert not any("side_threads" in p for p in by), "a recipe's slot condition has no single sample answer"
rows, _ = author_evaluate.evaluate_all(story, {})
assert not {r["path"]: r for r in rows}["plot.main_thread.acts[0].requires"]["satisfied"], "the seed (-20) applies"
print("OK: the sample state reads seeds and sample bond scores; recipe conditions are left out of it")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for slug in ("example", os.path.join("private", "the_missing_core"), os.path.join("private", "new_babel")):
    path = os.path.join(REPO, "stories", slug, "template.json")
    if not os.path.exists(path):
        continue
    raw = json.load(open(path, encoding="utf-8"))
    out = author_model.from_board_model(raw, author_model.to_board_model(raw))
    assert out.get("mechanics", {}).get("bonds") == raw.get("mechanics", {}).get("bonds")
    assert out.get("mechanics", {}).get("side_threads") == raw.get("mechanics", {}).get("side_threads")
print("OK: real stories round-trip with no CR-11 block added")

print("\nALL CHECKS PASSED: test_author_cr11")
