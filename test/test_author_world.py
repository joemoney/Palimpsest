"""The storyboard's World tab on the server side: meta, world (setting, rules, locations,
factions, the opening location) and CR-06 lore (`mechanics.lore`) through the board model and
back, plus the lint that goes with them.

Run directly: python3 test/test_author_world.py
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

RAW = {
    "schema_version": 3,
    "meta": {"title": "T", "genre": "g", "synopsis": "s", "content_rules": ["adults only"]},
    "world": {
        "setting_summary": "A drowned coast.",
        "rules": ["No magic.", "The sea is never wrong."],
        "locations": {
            "loc_a": {"name": "A", "description": "a", "connected_to": ["loc_b"], "_note": "keep me"},
            "loc_b": {"name": "B", "description": "b", "connected_to": ["loc_a"]},
        },
        "factions": {"f_board": {"name": "The Board", "goals": "tax everything",
                                 "relationship_to_player": "your creditor"}},
        "characters": {"Lark": {"name": "Lark", "description": "a diver"}},
    },
    "mechanics": {"gate": {"engine": "precondition", "gates": [
        {"id": "g1", "target": "loc_b", "requires": {"turn_gte": 5}}]}},
    "plot": {"initial_scene": {"location": "loc_a", "summary": "Dawn."}},
}


def model_of(raw):
    return copy.deepcopy(author_model.to_board_model(raw))


# --- load ------------------------------------------------------------------------------------
m = model_of(RAW)
assert m["meta"] == {"title": "T", "synopsis": "s", "genre": "g", "tone": "", "content_rules": ["adults only"]}
assert m["world"]["rules"] == ["No magic.", "The sea is never wrong."]
assert [l["id"] for l in m["world"]["locations"]] == ["loc_a", "loc_b"]
assert m["world"]["factions"][0]["relationship_to_player"] == "your creditor"
assert m["world"]["start_location"] == "loc_a"
assert m["lore"] == {"max_active": None, "entries": []}
print("OK: meta, world and lore load into the board model")

# --- untouched: nothing changes ------------------------------------------------------------------
out = author_model.from_board_model(RAW, m)
for key in ("meta", "world"):
    assert out[key] == RAW[key], key
assert out["mechanics"] == RAW["mechanics"] and out["plot"]["initial_scene"] == RAW["plot"]["initial_scene"]
print("OK: an untouched World tab leaves meta, world, gates and the opening scene as they were")

# --- meta edits, blank optional fields drop -------------------------------------------------------
m = model_of(RAW)
m["meta"].update(title="New Title", tone="wry", genre="  ", content_rules=["adults only", " "])
out = author_model.from_board_model(RAW, m)
assert out["meta"] == {"title": "New Title", "synopsis": "s", "content_rules": ["adults only"], "tone": "wry"}, out["meta"]
print("OK: meta edits write through; a blank genre or content rule is dropped, not stored empty")

# --- a location rename follows through everywhere a location id is written ------------------------
m = model_of(RAW)
m["world"]["locations"][1]["id"] = "loc_bay"
out = author_model.from_board_model(RAW, m)
locs = out["world"]["locations"]
assert list(locs) == ["loc_a", "loc_bay"], list(locs)
assert locs["loc_a"]["connected_to"] == ["loc_bay"] and locs["loc_a"]["_note"] == "keep me"
assert out["mechanics"]["gate"]["gates"][0]["target"] == "loc_bay", "a gate must follow its location"
m["world"]["locations"][0]["id"] = "loc_arrival"
m["world"]["start_location"] = "loc_a"   # the client sends the old id if it didn't cascade itself
out = author_model.from_board_model(RAW, m)
assert out["plot"]["initial_scene"]["location"] == "loc_arrival"
assert out["world"]["locations"]["loc_bay"]["connected_to"] == ["loc_arrival"]
assert out["world"]["characters"] == RAW["world"]["characters"], "the Cast tab's characters are untouched"
assert author_lint.world_issues(out) == []
print("OK: renaming a location updates connections, the opening location and gate targets")

# --- factions: add, blank fields drop, delete all removes the key ---------------------------------
m = model_of(RAW)
m["world"]["factions"].append({"orig": "", "id": "f_new", "name": "New", "goals": "", "relationship_to_player": ""})
out = author_model.from_board_model(RAW, m)
assert out["world"]["factions"]["f_new"] == {"name": "New"}
m["world"]["factions"] = []
m["world"]["locations"] = []
out = author_model.from_board_model(RAW, m)
assert "factions" not in out["world"] and "locations" not in out["world"]
print("OK: factions add and drop blank fields; emptied locations/factions are removed (P-2)")

# --- lore: a first entry declares keyed_lore at its final path (D1); emptying removes it ----------
m = model_of(RAW)
m["lore"]["max_active"] = 3
m["lore"]["entries"].append({"orig": "", "id": "lark_fixed", "priority": 90, "keys": ["Lark", "Ferris"],
                             "also_when": {"flag": "lark_aboard"}, "sticky_turns": 3,
                             "content": "Lark is fixed for the whole story."})
out = author_model.from_board_model(RAW, m)
assert out["mechanics"]["lore"] == {"engine": "keyed_lore", "max_active": 3, "entries": [
    {"id": "lark_fixed", "priority": 90, "keys": ["Lark", "Ferris"], "also_when": {"flag": "lark_aboard"},
     "sticky_turns": 3, "content": "Lark is fixed for the whole story."}]}, out["mechanics"]["lore"]
lore_schema = [e for e in author_lint.schema_errors(out) if e["message"].startswith("mechanics")]
assert lore_schema == [], lore_schema  # the fixture is minimal; only the lore block is under test
paths = {p: pol for p, _, pol, _ in conditions.iter_conditions(out)}
assert paths["mechanics.lore.entries[lark_fixed].also_when"] == conditions.CLOSED
m2 = model_of(out)
assert m2["lore"]["entries"][0]["id"] == "lark_fixed"
assert author_model.from_board_model(out, m2)["mechanics"]["lore"] == out["mechanics"]["lore"]
m2["lore"]["entries"] = []
assert "lore" not in author_model.from_board_model(out, m2)["mechanics"]
print("OK: lore writes mechanics.lore with keyed_lore, validates, round-trips, and its conditions "
      "are read CLOSED")

# --- lint -----------------------------------------------------------------------------------------
bad = copy.deepcopy(RAW)
bad["world"]["locations"]["loc_a"]["connected_to"] = ["loc_gone"]
bad["plot"]["initial_scene"]["location"] = "loc_nowhere"
bad["mechanics"]["gate"]["gates"][0]["target"] = "loc_missing"
bad["mechanics"]["lore"] = {"engine": "keyed_lore", "entries": [
    {"id": "x", "keys": ["the"], "content": "c"}, {"id": "y", "keys": ["Lark"], "content": ""},
    {"id": "z", "keys": ["lark"], "content": "c"}, {"id": "z", "content": "c"}]}
msgs = [(i["id"], i["severity"], i["message"]) for i in author_lint.world_issues(bad)]
def has(id_, sev, text):
    return any(i == id_ and s == sev and text in m for i, s, m in msgs)
assert has("L16", "error", "connects to loc_gone"), msgs
assert has("L16", "error", "opening scene is at loc_nowhere"), msgs
assert has("L16", "error", "guards loc_missing"), msgs
assert has("L16", "error", "Lore entry z is authored twice"), msgs
assert has("L13", "warning", '"the"'), msgs
assert has("L13", "warning", "shared by y, z"), msgs
assert has("lore", "warning", "y has no content"), msgs
assert has("lore", "warning", "z has no keys and no also_when"), msgs
print("OK: lint flags dangling location ids (connections, opening scene, gates), duplicate lore "
      "ids, generic or shared keys, and lore that can never trigger")

# --- the real stories, when present: meta/world round-trip untouched and lint clean ---------------
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for slug in ("example", os.path.join("private", "the_missing_core"), os.path.join("private", "new_babel")):
    path = os.path.join(REPO, "stories", slug, "template.json")
    if not os.path.exists(path):
        print(f"SKIPPED (real story): stories/{slug} is not checked out")
        continue
    raw = json.load(open(path, encoding="utf-8"))
    out = author_model.from_board_model(raw, author_model.to_board_model(raw))
    assert out["meta"] == raw["meta"] and out["world"] == raw["world"], slug
    assert author_lint.world_issues(raw) == [], (slug, author_lint.world_issues(raw))
    print(f"OK: stories/{slug} world round-trips untouched and lints clean")

print("\nALL CHECKS PASSED: test_author_world")
