"""A thread's authored cast (`plot.subplots.<id>.cast`): the storyboard's link between characters
and threads. Author-only - no engine reads it and it is never prompted - so what matters here is
that it round-trips, is dropped when empty, and that a name it holds is a real character (L16).

Run directly: python3 test/test_author_thread_cast.py
"""
import copy
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _llm_stubs  # noqa: E402,F401
import author_lint  # noqa: E402
import author_model  # noqa: E402

RAW = {
    "schema_version": 3,
    "meta": {"title": "T"},
    "world": {"characters": {"Lark Ferris": {"name": "Lark Ferris", "description": "a diver"},
                             "Descant": {"name": "Descant", "description": "the ship"}}},
    "plot": {"subplots": {
        "sp_diver": {"title": "The Diver", "description": "d", "starts_active": True, "role": "personal",
                     "cast": ["Lark Ferris"]},
        "sp_orders": {"title": "The Standing Orders", "description": "o", "starts_active": True},
    }},
}

model = author_model.to_board_model(RAW)
nodes = {n["id"]: n for n in model["nodes"]}
assert nodes["sp_diver"]["cast"] == ["Lark Ferris"] and "cast" not in nodes["sp_orders"]
out = author_model.from_board_model(RAW, model)
assert out["plot"]["subplots"] == RAW["plot"]["subplots"], out["plot"]["subplots"]
print("OK: a thread's cast loads onto its node and round-trips untouched")

m = copy.deepcopy(model)
for n in m["nodes"]:
    if n["id"] == "sp_orders":
        n["cast"] = ["Descant", "Lark Ferris"]
    if n["id"] == "sp_diver":
        n["cast"] = []
out = author_model.from_board_model(RAW, m)
assert out["plot"]["subplots"]["sp_orders"]["cast"] == ["Descant", "Lark Ferris"]
assert "cast" not in out["plot"]["subplots"]["sp_diver"], "an emptied cast is absent, not [] (P-2)"
print("OK: ticking characters writes the cast in order; unticking them all removes the key")

assert author_lint.thread_cast_issues(RAW) == []
bad = copy.deepcopy(RAW)
bad["plot"]["subplots"]["sp_diver"]["cast"] = ["Lark Ferris", "Lark"]
issues = author_lint.thread_cast_issues(bad)
assert [(i["id"], i["severity"]) for i in issues] == [("L16", "error")], issues
assert "The Diver casts Lark" in issues[0]["message"], issues
print("OK: a cast naming someone who is not a character is an L16 error")

print("\nALL CHECKS PASSED: test_author_thread_cast")
