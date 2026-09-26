"""CR-07 thread completion rewards, authoring side: `mechanics.subplots.completion_rewards` and
`near_completion_margin` in the schema, a thread's own `on_complete` and the block round-tripping
through the board untouched, lint (an event no axis prices, a margin with nothing to foreshadow),
and a load warning while no engine pays them.

Run directly: python3 test/test_thread_rewards_authoring.py
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
import mechanics  # noqa: E402

RAW = {"schema_version": 3, "meta": {"title": "T"},
       "plot": {"subplots": {"s1": {"title": "Relight the node", "description": "d", "priority": "high",
                                    "completion_threshold": 10, "starts_active": True,
                                    "on_complete": {"stat_events": ["node.relit"]}},
                             "s2": {"title": "Odd jobs", "description": "d", "priority": "low",
                                    "completion_threshold": 5, "starts_active": True}}},
       "mechanics": {"stats": {"engine": "bounded_counter",
                               "axes": {"lattice": {"costs": {"node.relit": 5, "section.reclaimed": 10}}}},
                     "subplots": {"engine": "weighted_threads",
                                  "completion_rewards": {"high": ["section.reclaimed"], "low": []},
                                  "near_completion_margin": 15}}}

subs_errors = lambda raw: [e["message"] for e in author_lint.schema_errors(raw) if "subplots" in e["message"]]  # noqa: E731
assert subs_errors(RAW) == [], subs_errors(RAW)
bad = copy.deepcopy(RAW)
bad["mechanics"]["subplots"]["completion_rewards"]["high"] = "section.reclaimed"
bad["mechanics"]["subplots"]["near_completion_margin"] = 0
assert len(subs_errors(bad)) == 2, subs_errors(bad)
print("OK: completion_rewards and near_completion_margin are schema-checked")

model = author_model.to_board_model(RAW)
out = author_model.from_board_model(RAW, model)
assert out["mechanics"]["subplots"] == RAW["mechanics"]["subplots"]
assert out["plot"]["subplots"]["s1"]["on_complete"] == RAW["plot"]["subplots"]["s1"]["on_complete"]
print("OK: rewards, margin and a thread's own on_complete round-trip through the board untouched")

assert author_lint.thread_reward_issues(RAW) == []
x = copy.deepcopy(RAW)
x["mechanics"]["subplots"]["completion_rewards"]["medium"] = ["node.typo"]
x["plot"]["subplots"]["s2"]["on_complete"] = {"stat_events": ["lattice.rejoined"]}
got = [(i["severity"], i["message"]) for i in author_lint.thread_reward_issues(x)]
assert len(got) == 2 and all(s == "error" for s, _ in got), got
assert any("medium-priority" in m and "node.typo" in m for _, m in got), got
assert any("Odd jobs" in m and "lattice.rejoined" in m for _, m in got), got
unpriced = copy.deepcopy(RAW)
del unpriced["mechanics"]["stats"]
assert any("authors no axis costs" in i["message"] for i in author_lint.thread_reward_issues(unpriced))
idle = copy.deepcopy(RAW)
idle["mechanics"]["subplots"]["completion_rewards"] = {}
del idle["plot"]["subplots"]["s1"]["on_complete"]
assert [i["severity"] for i in author_lint.thread_reward_issues(idle)] == ["warning"]
assert any(i["id"] == "mechanics.subplots" for i in author_lint.thread_reward_issues(idle))
print("OK: lint flags events no axis prices, an unpriced story, and a margin with no reward")

for label, raw in (("completion_rewards", RAW), ("a thread's on_complete", {**RAW, "mechanics": {"subplots": {"engine": "weighted_threads"}}})):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        mechanics.validate(raw)
    assert "CR-07" in buf.getvalue(), label
plain = copy.deepcopy(RAW)
plain["plot"]["subplots"]["s1"].pop("on_complete")
plain["mechanics"]["subplots"] = {"engine": "weighted_threads"}
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    mechanics.validate(plain)
assert "CR-07" not in buf.getvalue()
print("OK: an authored reward warns at load while no engine pays it; a story without one stays quiet")

print("\nALL CHECKS PASSED: test_thread_rewards_authoring")
