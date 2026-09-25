"""The Diagram tab's acts strip (`plot.main_thread`) through the board model and back: untouched
it changes nothing; edits patch each act's original entry (so `requires` survives); acts are
renumbered 1..n in board order; a blank `max_acts` is dropped.

Run directly: python3 test/test_author_acts.py
"""
import copy
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _llm_stubs  # noqa: E402,F401
import author_model  # noqa: E402

RAW = {"schema_version": 3, "meta": {"title": "T"}, "plot": {"main_thread": {
    "title": "Deliver the letter", "description": "Get it to Harrowgate.", "plot_notes": "keep me",
    "acts": [
        {"act_number": 1, "title": "The Quay", "description": "Leave port.",
         "completion_signals": ["the ferry has sailed"], "requires": {"flag": "boarded"}},
        {"act_number": 2, "title": "The Tide Road", "description": "Cross before it closes."},
    ]}}}

m = author_model.to_board_model(RAW)["main_thread"]
assert m == {"title": "Deliver the letter", "description": "Get it to Harrowgate.", "plot_notes": "keep me",
             "max_acts": None, "acts": [
    {"orig": 0, "title": "The Quay", "description": "Leave port.", "completion_signals": ["the ferry has sailed"],
     "requires": {"flag": "boarded"}},
    {"orig": 1, "title": "The Tide Road", "description": "Cross before it closes.", "completion_signals": [],
     "requires": None}]}, m
full = author_model.to_board_model(RAW)
assert author_model.from_board_model(RAW, full)["plot"]["main_thread"] == RAW["plot"]["main_thread"]
print("OK: the acts strip loads the main thread and round-trips it untouched")

full = copy.deepcopy(author_model.to_board_model(RAW))
mt = full["main_thread"]
mt["max_acts"] = 6
mt["acts"].reverse()                                   # move The Tide Road first
mt["acts"][1]["title"] = "The Quay at Dawn"
mt["acts"].append({"orig": None, "title": "Harrowgate", "description": "Arrive.", "completion_signals": [" "]})
out = author_model.from_board_model(RAW, full)["plot"]["main_thread"]
assert out["max_acts"] == 6 and out["plot_notes"] == "keep me"
assert [(a["act_number"], a["title"]) for a in out["acts"]] == [(1, "The Tide Road"), (2, "The Quay at Dawn"), (3, "Harrowgate")]
assert out["acts"][1]["requires"] == {"flag": "boarded"}, "an edited act keeps the fields the strip can't edit"
assert "completion_signals" not in out["acts"][2], "a blank signal is dropped, and with it the empty list"
full["main_thread"]["max_acts"] = None
assert "max_acts" not in author_model.from_board_model(RAW, full)["plot"]["main_thread"]
full["main_thread"]["acts"][0]["requires"] = {"turn_gte": 12}
full["main_thread"]["plot_notes"] = ""
out = author_model.from_board_model(RAW, full)["plot"]["main_thread"]
assert out["acts"][0]["requires"] == {"turn_gte": 12} and "plot_notes" not in out
print("OK: reordering renumbers acts, edits patch their originals (requires survives), blanks drop; "
      "requires and plot_notes are editable")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for slug in ("example", os.path.join("private", "the_missing_core"), os.path.join("private", "new_babel")):
    path = os.path.join(REPO, "stories", slug, "template.json")
    if not os.path.exists(path):
        print(f"SKIPPED (real story): stories/{slug} is not checked out")
        continue
    raw = json.load(open(path, encoding="utf-8"))
    out = author_model.from_board_model(raw, author_model.to_board_model(raw))
    assert out["plot"]["main_thread"] == raw["plot"]["main_thread"], slug
    print(f"OK: stories/{slug} main thread round-trips untouched")

print("\nALL CHECKS PASSED: test_author_acts")
