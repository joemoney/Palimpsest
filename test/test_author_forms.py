"""The storyboard's Forms tab (Authoring_Tool_Spec.md §5): every template section with no
dedicated editor is carried whole in the board model (`forms`) and written back only when changed.
Also the guard that keeps the storyboard complete: every section the schema defines must be
editable somewhere on the board.

Run directly: python3 test/test_author_forms.py
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

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FORM_PATHS = [p for p, _ in author_model.FORM_SECTIONS]

# --- coverage: every schema section has an editor on the board ----------------------------------
# Dedicated editors, by the template path they own: the World tab (meta, world.*), the Cast tab
# (world.characters), the Diagram tab (plot.main_thread acts strip, plot.subplots lanes, endings,
# terminals from legacy failure_conditions, the stat tier ladder), the Fragments tab
# (mechanics.revelations), the Story health panel (mechanics.flags), lore in the World tab, bonds
# in the Cast tab and the Side threads tab (CR-11), derived values on the Protagonist card (CR-04).
DEDICATED = {
    "meta", "world", "plot.main_thread", "plot.subplots", "mechanics.revelations",
    "mechanics.endings", "mechanics.failure_conditions", "mechanics.flags", "mechanics.lore",
    "mechanics.bonds", "mechanics.side_threads", "derived",
}
schema = author_lint.template_schema()
defs = schema["$defs"]
resolve = lambda n: defs[n["$ref"].split("/")[-1]] if "$ref" in n else n  # noqa: E731
sections = []
for top in schema["properties"]:
    if top in ("schema_version", "story_version"):
        continue  # written by the save flow itself
    node = resolve(schema["properties"][top])
    if top in ("plot", "mechanics"):
        sections += [f"{top}.{k}" for k in node.get("properties", {})]
    else:
        sections.append(top)
uncovered = [s for s in sections if s not in DEDICATED and s not in FORM_PATHS]
assert not uncovered, f"schema sections with no editor on the storyboard: {uncovered}"
assert not [p for p in FORM_PATHS if p in DEDICATED], "a section has two editors"
print(f"OK: all {len(sections)} schema sections are editable on the board "
      f"({len(FORM_PATHS)} in the Forms tab, the rest in dedicated views)")

# --- untouched forms change nothing, for every real story and fixture ----------------------------
paths = [os.path.join(REPO, "stories", s, "template.json")
         for s in ("example", os.path.join("private", "the_missing_core"), os.path.join("private", "new_babel"))]
paths += [os.path.join(REPO, "test", "fixtures", f) for f in ("regency.json", "courtroom.json", "survival.json")]
checked = 0
for path in paths:
    if not os.path.exists(path):
        continue
    raw = json.load(open(path, encoding="utf-8"))
    model = author_model.to_board_model(raw)
    for p in FORM_PATHS:
        assert model["forms"][p] == author_model._get_path(raw, p), (path, p)
    out = author_model.from_board_model(raw, model)
    skip = ("schema_version", "_storyboard")  # positions are layout, as in test_author_model
    assert {k: v for k, v in out.items() if k not in skip} == \
        {k: v for k, v in raw.items() if k not in skip}, path
    checked += 1
print(f"OK: an untouched Forms tab changes nothing in {checked} templates")

# --- an edit writes its section; None removes it; an absent section can be added ----------------
RAW = {"schema_version": 3, "meta": {"title": "T"},
       "narration": {"pov": "second", "style": ["terse"]},
       "plot": {"initial_scene": {"location": "loc_a", "summary": "Dawn."}, "pacing": {"nudge_frequency": 8, "act_check_frequency": 12}},
       "mechanics": {"stats": {"engine": "bounded_counter", "floor": 0, "ceiling": 10,
                               "axes": {"nerve": {"tiers": [{"at": 0, "label": "shaken"}]}}}}}
m = copy.deepcopy(author_model.to_board_model(RAW))
m["forms"]["narration"]["pov"] = "first"
m["forms"]["plot.pacing"] = None
m["forms"]["mechanics.gate"] = {"engine": "precondition", "gates": [{"target": "loc_a", "requires": {"turn_gte": 3}}]}
out = author_model.from_board_model(RAW, m)
assert out["narration"] == {"pov": "first", "style": ["terse"]}
assert "pacing" not in out["plot"]
assert out["mechanics"]["gate"]["gates"][0]["target"] == "loc_a"
print("OK: a changed section is written, a cleared one removed, a new one added")

# --- owned parts: the tier ladder and the World tab still win over a stale form copy -------------
m = copy.deepcopy(author_model.to_board_model(RAW))
m["forms"]["mechanics.stats"]["ceiling"] = 20                       # a Forms edit ...
m["stat_axes"][0]["tiers"] = [{"at": 0, "label": "shaken"}, {"at": 5, "label": "steady"}]  # ... and a ladder edit
m["forms"]["plot.initial_scene"]["summary"] = "Dusk."
m["world"]["start_location"] = "loc_b"
out = author_model.from_board_model(RAW, m)
assert out["mechanics"]["stats"]["ceiling"] == 20
assert [t["label"] for t in out["mechanics"]["stats"]["axes"]["nerve"]["tiers"]] == ["shaken", "steady"]
assert out["plot"]["initial_scene"] == {"location": "loc_b", "summary": "Dusk."}
print("OK: Forms edits and the tier ladder / World tab edits to the same section both land")

# --- inventory items: a label or an item record, and the record's shape is the engine's -----------
def inv_errors(items):
    raw = {"protagonist": {"default_name": "X", "starting_inventory": items}}
    return [e["message"] for e in author_lint.schema_errors(raw) if e["message"].startswith("protagonist")]
assert inv_errors(["a brass key", {"id": "itm_001", "label": "a cutter", "tags": ["tool"], "uses": 3}]) == []
assert inv_errors([{"label": "a cutter", "colour": "red"}]), "an unknown key on an item record is caught"
assert inv_errors([{"id": "itm_001"}]), "an item record needs a label"
print("OK: starting inventory accepts a label or an {id, label, tags, uses} record, and nothing else")

print("\nALL CHECKS PASSED: test_author_forms")
