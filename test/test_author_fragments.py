"""The storyboard's Fragments tab (`mechanics.revelations`) on the server side: the board-model
round trip, patch-don't-regenerate on save, fragment lint, and the labels that make a
`revealed` leaf read as what the fragment is rather than a bare id.

Run directly: python3 test/test_author_fragments.py
"""
import copy
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _llm_stubs  # noqa: E402,F401  (stubs filelock etc. before backend imports)
import author_lint  # noqa: E402
import author_model  # noqa: E402
import conditions  # noqa: E402

RAW = {
    "schema_version": 3,
    "meta": {"title": "T"},
    "mechanics": {"revelations": {"engine": "triggered_reveal", "entries": [
        {"id": "frag_1", "trigger": "the protagonist touches a bare Consonance surface with skin",
         "content": "It is exactly as warm as you are.", "_note": "an author field the tab has no editor for"},
        {"id": "frag_2", "trigger": "t2", "content": "c2", "after": ["frag_1"], "_title": "the lost hours"},
    ]}},
    "plot": {"subplots": {"sp": {"title": "S", "starts_active": False,
                                 "activate_when": {"revealed": "frag_2"}}}},
}

# --- to_board_model: one row per fragment, title from _title ------------------------------------
model = author_model.to_board_model(RAW)
assert model["revelations"] == [
    {"orig": "frag_1", "id": "frag_1", "title": "", "trigger": RAW["mechanics"]["revelations"]["entries"][0]["trigger"],
     "content": "It is exactly as warm as you are.", "after": []},
    {"orig": "frag_2", "id": "frag_2", "title": "the lost hours", "trigger": "t2", "content": "c2",
     "after": ["frag_1"]},
], model["revelations"]
assert author_model.to_board_model({"meta": {}})["revelations"] == []
print("OK: fragments load into the board model, titled from the older _title spelling")

# `title` is the schema's field (what a hand-edited template naturally uses - The Missing Core's
# fragments are titled this way); it loads, wins over _title, and passes the schema.
titled = copy.deepcopy(RAW)
titled["mechanics"]["revelations"]["entries"][0]["title"] = "Kept Warm"
titled["mechanics"]["revelations"]["entries"][1]["title"] = "Standing Up"
rows = author_model.to_board_model(titled)["revelations"]
assert [r["title"] for r in rows] == ["Kept Warm", "Standing Up"], rows
assert not [e for e in author_lint.schema_errors(titled) if "revelations" in e["message"]], author_lint.schema_errors(titled)
assert conditions.revelation_labels(titled)["frag_1"] == "Kept Warm"
print("OK: a fragment's `title` loads, outranks _title, labels its conditions, and is schema-valid")

# --- an unchanged list touches nothing ----------------------------------------------------------
assert author_model.from_board_model(RAW, model)["mechanics"]["revelations"] == RAW["mechanics"]["revelations"]
print("OK: an untouched Fragments tab leaves mechanics.revelations byte-for-byte as it was")

# --- edits patch the original entry; unknown author fields survive ------------------------------
m = copy.deepcopy(model)
m["revelations"][0]["title"] = "first warmth"
m["revelations"][0]["id"] = "frag_warm"          # renamed: `orig` still finds its entry
m["revelations"][1]["after"] = ["frag_warm"]
m["revelations"][1]["title"] = "  "              # blank title removes the key
out = author_model.from_board_model(RAW, m)
entries = out["mechanics"]["revelations"]["entries"]
assert entries[0] == {"id": "frag_warm", "trigger": RAW["mechanics"]["revelations"]["entries"][0]["trigger"],
                      "content": "It is exactly as warm as you are.",
                      "_note": "an author field the tab has no editor for", "title": "first warmth"}, entries[0]
assert entries[1] == {"id": "frag_2", "trigger": "t2", "content": "c2", "after": ["frag_warm"]}, entries[1]
assert out["mechanics"]["revelations"]["engine"] == "triggered_reveal"
assert RAW["mechanics"]["revelations"]["entries"][0]["id"] == "frag_1", "the input is never mutated"
print("OK: an edit patches its own entry (matched by orig), keeps unedited author fields, and "
      "drops a blank title")

# --- a new fragment in a story with none declares the engine; emptying removes the block --------
bare = {"schema_version": 3, "meta": {"title": "T"}, "mechanics": {}}
m = author_model.to_board_model(bare)
m["revelations"].append({"orig": "", "id": "frag_0001", "title": "", "trigger": "t", "content": "c", "after": []})
m["revelations"].append({"orig": "", "id": "  ", "title": "", "trigger": "", "content": "", "after": []})
out = author_model.from_board_model(bare, m)
assert out["mechanics"]["revelations"] == {"engine": "triggered_reveal", "entries": [
    {"id": "frag_0001", "trigger": "t", "content": "c"}]}, out["mechanics"]
m = author_model.to_board_model(RAW)
m["revelations"] = []
assert "revelations" not in author_model.from_board_model(RAW, m)["mechanics"]
print("OK: the first fragment declares triggered_reveal; a row with no id is dropped; emptying "
      "the tab removes the block (P-2)")

# --- lint ---------------------------------------------------------------------------------------
def fragment_issues(entries):
    return author_lint.revelation_issues({"mechanics": {"revelations": {"engine": "triggered_reveal",
                                                                        "entries": entries}}})

assert fragment_issues(RAW["mechanics"]["revelations"]["entries"]) == []
issues = fragment_issues([
    {"id": "a", "trigger": "t", "content": "c"}, {"id": "a", "trigger": "t", "content": "c"},
    {"id": "b", "trigger": "", "content": "", "after": ["ghost"]},
    {"id": "x", "trigger": "t", "content": "c", "after": ["y"]},
    {"id": "y", "trigger": "t", "content": "c", "after": ["x"]},
])
msgs = [(i["severity"], i["message"]) for i in issues]
assert ("error", "Fragment a is authored twice.") in msgs, msgs
assert any(s == "error" and "ghost" in m for s, m in msgs), msgs
assert any(s == "warning" and "b has no trigger" in m for s, m in msgs), msgs
assert any(s == "warning" and "b has no content" in m for s, m in msgs), msgs
assert any(s == "error" and "x waits on itself" in m for s, m in msgs), msgs
print("OK: lint flags duplicate ids, a dangling or circular 'after', and an empty trigger/content")

# --- labels: a revealed leaf reads by title, else trigger, never content ------------------------
labels = conditions.revelation_labels(RAW)
assert labels["frag_2"] == "the lost hours"
assert labels["frag_1"].startswith("the protagonist touches") and labels["frag_1"].endswith("…")
assert len(labels["frag_1"]) <= conditions.FRAGMENT_LABEL_MAX
assert "warm as you are" not in labels["frag_1"]
names = {"revealed": labels}
assert conditions.describe({"revealed": "frag_2"}, names=names) == "revealed: “the lost hours”"
assert conditions.describe({"not": {"revelation": "frag_2"}}, names=names) == "not revealed: “the lost hours”"
assert conditions.describe({"revealed": "frag_9"}, names=names) == "revealed: frag_9", "unknown id stays an id"
assert conditions.describe({"revealed": "frag_2"}) == "revealed: frag_2", "no names, no change"
edge = next(e for e in model_edges if e.get("cond_raw")) if (model_edges := author_model.to_board_model(RAW)["edges"]) else None
assert edge and edge["cond"] == "revealed: “the lost hours”", edge
print("OK: a revealed leaf is labelled by title, else trigger (never content), on edges and in describe")

# --- the real Missing Core, when its submodule is checked out ------------------------------------
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
mc = os.path.join(REPO, "stories", "private", "the_missing_core", "template.json")
if os.path.exists(mc):
    raw = json.load(open(mc, encoding="utf-8"))
    m = author_model.to_board_model(raw)
    assert m["revelations"] and author_model.from_board_model(raw, m)["mechanics"]["revelations"] == \
        raw["mechanics"]["revelations"]
    assert author_lint.revelation_issues(raw) == []
    print(f"OK: the Missing Core's {len(m['revelations'])} fragments round-trip and lint clean")
else:
    print("SKIPPED (real story): stories/private/the_missing_core is not checked out")

print("\nALL CHECKS PASSED: test_author_fragments")
