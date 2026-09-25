"""backend/author_model.py: template <-> board model (AUTHORING_TOOL_PHASES.md Phase S1).

Pure and I/O-free (no Flask, no state_store), so this test reads the real files directly
with plain json.load rather than through the offline-stub harness the rest of test/ uses -
there is no LLM call and no story/state split anywhere in this module to stub around.

Two things this test exists to prove, matching the S1 gate in AUTHORING_TOOL_PHASES.md:
1. Round trip: `from_board_model(raw, to_board_model(raw))` changes nothing except
   schema_version (bumped to 3) and `_storyboard.positions` (always rewritten) - checked
   against every real story and fixture, not a hand-picked subset.
2. A real edit changes exactly what it should and nothing else - the defence against a
   writer that "rebuilds from its own model" and calls the resulting diff a formatting
   change (the risk AUTHORING_TOOL_PHASES.md names for this exact module).

Run directly: python3 test/test_author_model.py
"""
import copy
import glob
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))
import author_model  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def real_template_paths():
    paths = (
        glob.glob(os.path.join(REPO_ROOT, "stories", "*", "template.json"))
        + glob.glob(os.path.join(REPO_ROOT, "stories", "private", "*", "template.json"))
        + glob.glob(os.path.join(REPO_ROOT, "test", "fixtures", "*.json"))
    )
    assert len(paths) >= 4, f"expected at least the public story and fixtures, found {paths}"
    return paths


# --- round trip: every real story and fixture, no edits, nothing lost -------------------
for path in real_template_paths():
    label = os.path.relpath(path, REPO_ROOT)
    raw = json.load(open(path, encoding="utf-8"))
    model = author_model.to_board_model(raw)
    written = author_model.from_board_model(raw, model)

    assert written["schema_version"] == author_model.TEMPLATE_SCHEMA_VERSION, label

    normalized_written = copy.deepcopy(written)
    normalized_written["schema_version"] = raw.get("schema_version")
    normalized_written.pop("_storyboard", None)
    normalized_original = copy.deepcopy(raw)
    normalized_original.pop("_storyboard", None)

    if normalized_written != normalized_original:
        # A targeted diff rather than a wall of JSON, so a real regression here is legible.
        def walk(a, b, path=""):
            if isinstance(a, dict) and isinstance(b, dict):
                for k in sorted(set(a) | set(b)):
                    walk(a.get(k, "<missing>"), b.get(k, "<missing>"), f"{path}.{k}")
            elif a != b:
                print(f"  {path}: {a!r} != {b!r}")
        print(f"MISMATCH in {label}:")
        walk(normalized_original, normalized_written)
        raise AssertionError(f"{label}: round trip through the board model changed content")
print(f"OK: {len(real_template_paths())} real template(s)/fixture(s) round-trip losslessly "
      "through to_board_model/from_board_model (only schema_version and _storyboard change)")

# --- a real edit: renaming a thread on the board changes only that thread's title --------
missing_core_path = os.path.join(REPO_ROOT, "stories", "private", "the_missing_core", "template.json")
raw = json.load(open(missing_core_path, encoding="utf-8"))
model = author_model.to_board_model(raw)
thread = next(n for n in model["nodes"] if n["kind"] == "subplot" and n["id"] == "subplot_005")
assert thread["title"] == "The Diver"
thread["title"] = "The Diver, Renamed"
written = author_model.from_board_model(raw, model)
assert written["plot"]["subplots"]["subplot_005"]["title"] == "The Diver, Renamed"
# Nothing else about that subplot moved - completion_threshold/span/ties_to_main_plot survive
# untouched even though no field on the board edits them yet.
for key in ("priority", "completion_threshold", "ties_to_main_plot", "span", "description"):
    assert written["plot"]["subplots"]["subplot_005"][key] == raw["plot"]["subplots"]["subplot_005"][key], key
# And every other subplot, and everything outside plot.subplots, is completely untouched.
for sid in raw["plot"]["subplots"]:
    if sid != "subplot_005":
        assert written["plot"]["subplots"][sid] == raw["plot"]["subplots"][sid], sid
assert written["world"] == raw["world"]
assert written["mechanics"] == raw["mechanics"]
print("OK: renaming a thread on the board changes only that thread's title")

# --- opens edges round-trip into starts_active (real content: subplot_001 authors it) ----
model2 = author_model.to_board_model(raw)
opens_edges = {(e["from"], e["to"]) for e in model2["edges"] if e["type"] == "opens"}
assert ("start", "subplot_001") in opens_edges, "subplot_001 authors starts_active: true"
written2 = author_model.from_board_model(raw, model2)
assert written2["plot"]["subplots"]["subplot_001"]["starts_active"] is True
print("OK: an opens edge loads from and writes back to starts_active")

# --- unlocks edges: no real story authors activate_when yet (CR-10 isn't built), so this is
# synthetic - constructing an unlocks edge on the board (an author connecting a thread with a
# condition) and confirming it writes activate_when in CR-10's shape. ---
sub_node = next(n for n in model2["nodes"] if n["id"] == "subplot_004")
assert "starts_active" not in sub_node or not sub_node["starts_active"], \
    "subplot_004 is not active at start"
# Drop any activation subplot_004 already authors (the real story now unlocks it) so this
# stays a test of the writer, not of whatever the story currently says.
model2["edges"] = [e for e in model2["edges"]
                   if not (e.get("to") == "subplot_004" and e["type"] in ("opens", "unlocks"))]
model2["edges"].append({"type": "unlocks", "from": "start", "to": "subplot_004",
                         "cond": "REACH >= 20",
                         "cond_raw": {"stat": {"axis": "reach", "at_least": 20}}})
written3 = author_model.from_board_model(raw, model2)
assert written3["plot"]["subplots"]["subplot_004"]["activate_when"] == {"stat": {"axis": "reach", "at_least": 20}}
assert "starts_active" not in written3["plot"]["subplots"]["subplot_004"], \
    "an unlocks edge clears any starts_active on the same subplot"
print("OK: adding an unlocks edge on the board writes activate_when, clearing starts_active")

# --- an unlocks edge with no condition writes no activate_when at all. It used to write a
# {"condition": "TODO"} placeholder, which is not CR-02 grammar and failed L01 on save. ---
example = json.load(open(os.path.join(REPO_ROOT, "stories", "example", "template.json"), encoding="utf-8"))
model_ex = author_model.to_board_model(example)
sid = next(n["id"] for n in model_ex["nodes"] if n["kind"] == "subplot")
model_ex["edges"] = [e for e in model_ex["edges"] if not (e.get("to") == sid and e["type"] in ("opens", "unlocks"))]
model_ex["edges"].append({"type": "unlocks", "from": "start", "to": sid})
written_ex = author_model.from_board_model(example, model_ex)
assert "condition" not in json.dumps(written_ex["plot"]["subplots"][sid].get("activate_when", {}))
assert "activate_when" not in written_ex["plot"]["subplots"][sid]
print("OK: a condition-less unlocks edge writes no activate_when (no TODO placeholder)")

# condition labels: real grammar renders readably, legacy free text still displays
assert author_model._condition_label({"stat": {"axis": "reach", "at_least": 20}}) == "REACH >= 20"
assert author_model._condition_label({"flag": "lark_departed"}) == "flag: lark_departed"
assert author_model._condition_label({"condition": "REACH >= 20"}) == "REACH >= 20"
print("OK: unlock-edge labels render CR-02 grammar and the legacy free-text shape")

# --- a brand-new thread (added on the board, not present in the original template) with an
# opens edge is written with starts_active: true - the dict entry for a new subplot doesn't
# exist yet at the point edges are matched against subplots, so this specifically guards
# against filtering opens/unlocks/delivers edges against the (pre-population) subplots dict
# instead of against every live thread id on the board. ---
model4 = author_model.to_board_model(raw)
model4["nodes"].append({"id": "subplot_new", "kind": "subplot", "title": "Brand New",
                         "theme": "a new thread", "role": "spine", "x": 0, "y": 0})
model4["edges"].append({"type": "opens", "from": "start", "to": "subplot_new"})
written4 = author_model.from_board_model(raw, model4)
assert written4["plot"]["subplots"]["subplot_new"]["starts_active"] is True
assert written4["plot"]["subplots"]["subplot_new"]["title"] == "Brand New"
print("OK: a brand-new thread's opens edge writes starts_active: true")

# --- failure_conditions surfaces as terminal nodes and round-trips (survival fixture) -----
survival_path = os.path.join(REPO_ROOT, "test", "fixtures", "survival.json")
raw_s = json.load(open(survival_path, encoding="utf-8"))
model_s = author_model.to_board_model(raw_s)
terminals = [n for n in model_s["nodes"] if n["kind"] == "ending" and n["ekind"] == "terminal"]
assert {n["id"] for n in terminals} == {"fail_cold", "fail_dark"}
assert next(n for n in terminals if n["id"] == "fail_cold")["trigger"] == \
    raw_s["mechanics"]["failure_conditions"]["conditions"][0]["trigger"]
# Editing one terminal's title changes only that condition's title in failure_conditions.
term = next(n for n in model_s["nodes"] if n["id"] == "fail_cold")
term["title"] = "Windward, Renamed"
written_s = author_model.from_board_model(raw_s, model_s)
conds = {c["id"]: c for c in written_s["mechanics"]["failure_conditions"]["conditions"]}
assert conds["fail_cold"]["title"] == "Windward, Renamed"
assert conds["fail_dark"] == raw_s["mechanics"]["failure_conditions"]["conditions"][1]
print("OK: failure_conditions terminals load, edit and write back without touching CR-05 shape")

# --- synthetic destination ending: waypoints and judge/author-only fields all round-trip,
# including fields no UI edits yet (viable_while, ready_when, hint, criteria, arc, epilogue) -
# none of today's real templates author mechanics.endings, so this is the only coverage for
# that path until one does. ---
synthetic = {
    "schema_version": 2, "story_version": "test.1",
    "meta": {"title": "Synthetic"}, "narration": {}, "world": {"rules": []},
    "protagonist": {},
    "mechanics": {
        "endings": {
            "engine": "ending_funnel",
            "budget": {"open_until": 40, "narrow_until": 90, "commit_by": 140},
            "entries": [
                {"id": "the_end", "kind": "destination", "name": "The End",
                 "_theme": "a synthetic ending",
                 "viable_while": {"not": {"flag": "gone"}},
                 "ready_when": {"stat": "quorum", "gte": 65},
                 "waypoints": [
                     {"id": "wp1", "plant": "a door opens", "detect": "the door is open"},
                 ],
                 "hint": "quiet, for now",
                 "criteria": "JUDGE-ONLY: the door has genuinely opened",
                 "arc": {"title": "The End", "description": "NARRATOR-FACING once committed."},
                 "epilogue": "UI-only text after THE END."},
            ],
        },
    },
    "plot": {
        "main_thread": {"title": "t", "description": "d", "acts": [
            {"act_number": 1, "title": "Act 1", "description": "d", "completion_signals": []}
        ]},
        "subplots": {
            "sp_001": {"title": "Carrier", "description": "d", "starts_active": True,
                       "delivers": ["the_end.wp1"]},
        },
        "pacing": {"nudge_frequency": 8, "act_check_frequency": 12, "max_parallel_subplots": 3},
        "opening_scene": {"narration_before_name": "", "narration_after_name": ""},
        "initial_scene": {"location": "", "summary": ""},
    },
}
model_e = author_model.to_board_model(synthetic)
ending_node = next(n for n in model_e["nodes"] if n["kind"] == "ending" and n["ekind"] == "destination")
assert ending_node["waypoints"] == [{"id": "wp1", "plant": "a door opens",
                                      "detect": "the door is open", "done_when": None}]
assert ending_node["catchAll"] is False, "the_end authors viable_while, so it is not a catch-all"
delivers_edges = [e for e in model_e["edges"] if e["type"] == "delivers"]
assert delivers_edges == [{"type": "delivers", "from": "sp_001", "to": "the_end", "wp": "wp1"}]

written_e = author_model.from_board_model(synthetic, model_e)
entry = written_e["mechanics"]["endings"]["entries"][0]
for field in ("viable_while", "ready_when", "hint", "criteria", "arc", "epilogue"):
    assert entry[field] == synthetic["mechanics"]["endings"]["entries"][0][field], field
assert written_e["plot"]["subplots"]["sp_001"]["delivers"] == ["the_end.wp1"]

# --- endings_settings: the mechanics.endings block's own config round-trips -----------------
assert model_e["endings_settings"] == {"budget": {"open_until": 40, "narrow_until": 90, "commit_by": 140}}
written_same = author_model.from_board_model(synthetic, model_e)
assert written_same["mechanics"]["endings"]["budget"] == synthetic["mechanics"]["endings"]["budget"]
assert "check_every" not in written_same["mechanics"]["endings"], "P-2: no key the board never sent"

edited_settings = dict(model_e)
edited_settings["endings_settings"] = {"budget": {"open_until": 40, "narrow_until": 90, "commit_by": 140},
                                        "check_every": 6, "steer_top": 2}
written_edit = author_model.from_board_model(synthetic, edited_settings)
assert written_edit["mechanics"]["endings"]["check_every"] == 6
assert written_edit["mechanics"]["endings"]["steer_top"] == 2
assert written_edit["mechanics"]["endings"]["budget"]["open_until"] == 40
print("OK: endings_settings (check_every/budget/steer_top/finale_turns) round-trips and patches in place")

no_settings_model = copy.deepcopy(model_e)
del no_settings_model["endings_settings"]
no_settings_synthetic = copy.deepcopy(synthetic)
del no_settings_synthetic["mechanics"]["endings"]["budget"]
written_none = author_model.from_board_model(no_settings_synthetic, no_settings_model)
assert "budget" not in written_none["mechanics"]["endings"], "no settings authored, none written"
print("OK: a story with no endings_settings authored gets no settings keys written")

print("OK: a synthetic destination ending's waypoints, delivers edge, and every judge/"
      "author-only field (viable_while, ready_when, hint, criteria, arc, epilogue) round-trip")

# Editing the waypoint's plant text changes only that field.
ending_node["waypoints"][0]["plant"] = "a door opens for someone else"
written_e2 = author_model.from_board_model(synthetic, model_e)
entry2 = written_e2["mechanics"]["endings"]["entries"][0]
assert entry2["waypoints"][0]["plant"] == "a door opens for someone else"
assert entry2["ready_when"] == synthetic["mechanics"]["endings"]["entries"][0]["ready_when"]
assert entry2["criteria"] == synthetic["mechanics"]["endings"]["entries"][0]["criteria"]
print("OK: editing a waypoint's plant text leaves every other ending field untouched")

# --- ending colour is deterministic across repeated loads (assigned by position among
# destinations, matching the prototype's own convention - not by hashing the id, which
# Python salts per process and would make the same ending change colour on every reload) ---
synthetic_two = copy.deepcopy(synthetic)
synthetic_two["mechanics"]["endings"]["entries"].append(
    {"id": "the_other_end", "kind": "destination", "name": "The Other End",
     "viable_while": {"flag": "still_open"}, "waypoints": []})
colors_run_1 = {n["id"]: n["color"] for n in author_model.to_board_model(synthetic_two)["nodes"]
                if n.get("ekind") == "destination"}
colors_run_2 = {n["id"]: n["color"] for n in author_model.to_board_model(synthetic_two)["nodes"]
                if n.get("ekind") == "destination"}
assert colors_run_1 == colors_run_2 == {"the_end": "e1", "the_other_end": "e2"}, colors_run_1
print("OK: ending colour is assigned by position and is identical across repeated loads")

# --- catch-all: a destination with no viable_while ---
synthetic2 = copy.deepcopy(synthetic)
del synthetic2["mechanics"]["endings"]["entries"][0]["viable_while"]
model_c = author_model.to_board_model(synthetic2)
assert next(n for n in model_c["nodes"] if n.get("ekind") == "destination")["catchAll"] is True
print("OK: an entry with no viable_while loads as a catch-all node")

# --- cast: characters load and write back through the D7 (first_contact) shape -----------
cast_synthetic = copy.deepcopy(synthetic)
cast_synthetic["world"]["characters"] = {
    "Vesper": {"name": "Vesper", "description": "a courier", "role": "ally",
               "first_contact": "wary", "hook": "at the docks",
               "canon": {"truth": "secretly a defector"}},
}
model_ch = author_model.to_board_model(cast_synthetic)
char = next(c for c in model_ch["characters"] if c["name"] == "Vesper")
assert char["first_contact"] == "wary"
assert char["canon"] == [{"k": "truth", "v": "secretly a defector"}]
char["hook"] = "at the docks, now with a crate"
written_ch = author_model.from_board_model(cast_synthetic, model_ch)
assert written_ch["world"]["characters"]["Vesper"]["hook"] == "at the docks, now with a crate"
assert written_ch["world"]["characters"]["Vesper"]["canon"] == {"truth": "secretly a defector"}
print("OK: the cast round-trips through the board model in the D7 (first_contact) shape")

# --- D1: playable_projection removes an unregistered mechanics engine, loudly reports it -
registered = {("subplots", "weighted_threads"), ("stats", "bounded_counter")}
projected, left_out = author_model.playable_projection(synthetic, registered)
assert "endings" not in projected.get("mechanics", {}), "ending_funnel is not registered"
assert left_out == [("endings", "ending_funnel")]
assert synthetic["mechanics"]["endings"]["entries"], "playable_projection must not mutate its input"
print("OK: playable_projection drops an unregistered engine's block and reports what it left out")

# A registered engine survives the projection untouched.
synthetic3 = copy.deepcopy(synthetic)
synthetic3["mechanics"]["subplots"] = {"engine": "weighted_threads"}
projected3, left_out3 = author_model.playable_projection(synthetic3, registered)
assert projected3["mechanics"]["subplots"] == {"engine": "weighted_threads"}
assert ("subplots", "weighted_threads") not in left_out3
print("OK: playable_projection leaves a registered engine's block untouched")

# --- declared flags (mechanics.flags.declared) round-trip through the board ---------------------
ex_flags = copy.deepcopy(example)
assert "flags" not in ex_flags["mechanics"], "the example story declares no flags"
m_flags = author_model.to_board_model(ex_flags)
assert m_flags["flags_declared"] == [] and m_flags["refs"]["flags"] == []
assert author_model.from_board_model(ex_flags, m_flags)["mechanics"] == ex_flags["mechanics"], \
    "no flags declared and none authored: mechanics is untouched, no empty block appears (P-2)"

m_flags["flags_declared"] = [{"id": "lark_departed", "detect": "Lark leaves for good"},
                             {"id": "  ", "detect": "a blank id is dropped"}]
w1 = author_model.from_board_model(ex_flags, m_flags)
assert w1["mechanics"]["flags"] == {"declared": [{"id": "lark_departed", "detect": "Lark leaves for good"}]}
assert author_model.to_board_model(w1)["refs"]["flags"] == ["lark_departed"]

w1["mechanics"]["flags"]["declared"][0]["_note"] = "author scratch"
m2 = author_model.to_board_model(w1)
m2["flags_declared"][0]["detect"] = "Lark walks out"
w2 = author_model.from_board_model(w1, m2)
assert w2["mechanics"]["flags"]["declared"][0] == {"id": "lark_departed", "detect": "Lark walks out",
                                                   "_note": "author scratch"}, "unknown keys on an entry survive"

m3 = author_model.to_board_model(w2)
m3["flags_declared"] = []
w3 = author_model.from_board_model(w2, m3)
assert "flags" not in w3["mechanics"], "emptying the list removes the block again"
assert author_model.from_board_model(w2, {**m3, "flags_declared": None}) == w2, \
    "a client that sends no flags_declared leaves the template alone"
print("OK: declared flags round-trip - add, edit (keeping unknown keys), remove, and stay absent when unused")

print("\nALL CHECKS PASSED: test_author_model")
