"""CR-10's authored thread lifecycle in play (AUTHORING_TOOL_PHASES.md S5, the first
demand-driven piece): `activate_when` starts a seeded thread once its condition holds, and
`fail_when` fails one. Both fields were written by the board and round-tripped by the
template, but until `story_engine.apply_thread_conditions` nothing read either one in play.

Polarity is the part most easily weakened by accident, so it gets its own cases: an unknown
referent in `activate_when` (OPEN) starts the thread, while the same referent in `fail_when`
(CLOSED) does not fail it.

Run directly: python3 test/test_thread_conditions.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _llm_stubs import load_story_engine  # noqa: E402

se = load_story_engine()


def make_ctx(seeds, flags_declared=None):
    """A fresh save of the default story with its subplots replaced by `seeds`, and the runtime
    records created the way `new_save_state` creates them. Derived from nothing the default
    story itself authors, so it holds whichever story DEFAULT_STORY_SLUG points at."""
    ctx = se.state_store.load_state("threadconds", se.state_store.DEFAULT_STORY_SLUG)
    story = se.state_store.thaw(ctx["story"])
    story["plot"]["subplots"] = seeds
    story.setdefault("mechanics", {}).pop("flags", None)
    if flags_declared is not None:
        story["mechanics"]["flags"] = {"declared": [{"id": f, "detect": f"{f} happens"}
                                                    for f in flags_declared]}
    ctx["story"] = se.state_store.freeze(story)
    ctx["state"]["plot"]["subplots"] = {
        sid: {"progress": 0,
              "status": "active" if seed.get("starts_active") else "not_started",
              "active": bool(seed.get("starts_active"))}
        for sid, seed in seeds.items()
    }
    ctx["state"]["plot"]["endgame"]["requested"] = False
    return ctx


def record(ctx, sid):
    return ctx["state"]["plot"]["subplots"][sid]


# --- activate_when: waits until the condition holds, then starts, then latches ---------------
ctx = make_ctx({
    "sp_a": {"title": "A", "description": "a", "starts_active": True},
    "sp_b": {"title": "B", "description": "b",
             "activate_when": {"subplot_status": {"sp_a": "progressed"}}},
})
out = se.apply_thread_conditions(ctx)
assert out == {"failed": [], "activated": []}, out
assert record(ctx, "sp_b")["status"] == "not_started" and not record(ctx, "sp_b")["active"]

record(ctx, "sp_a")["progress"] = 20
out = se.apply_thread_conditions(ctx)
assert out["activated"] == ["sp_b"], out
assert record(ctx, "sp_b")["status"] == "active" and record(ctx, "sp_b")["active"] is True

# Latching: the condition going false again doesn't stop a thread that has started.
record(ctx, "sp_a")["progress"] = 0
out = se.apply_thread_conditions(ctx)
assert out["activated"] == [] and record(ctx, "sp_b")["active"] is True
print("OK: activate_when starts a thread once its condition holds, and never re-gates it")

# --- activate_when: a stat threshold, the Missing Core's "REACH >= 20" shape ------------------
ctx = make_ctx({"sp_s": {"title": "S", "description": "s",
                         "activate_when": {"stat": "reach", "gte": 20}}})
ctx["state"]["protagonist"].setdefault("stats", {})["reach"] = 5
se.apply_thread_conditions(ctx)
assert record(ctx, "sp_s")["status"] == "not_started"
ctx["state"]["protagonist"]["stats"]["reach"] = 20
se.apply_thread_conditions(ctx)
assert record(ctx, "sp_s")["status"] == "active"
print("OK: a stat-threshold activate_when starts the thread at the threshold, not before")

# --- polarity: unknown referent opens activate_when, but never closes a thread ---------------
ctx = make_ctx({
    "sp_typo_open": {"title": "T", "description": "t",
                     "activate_when": {"subplot_status": {"sp_does_not_exist": "completed"}}},
    "sp_typo_fail": {"title": "U", "description": "u", "starts_active": True,
                     "fail_when": {"subplot_status": {"sp_does_not_exist": "completed"}}},
})
out = se.apply_thread_conditions(ctx)
assert out["activated"] == ["sp_typo_open"], out
assert out["failed"] == [], out
assert record(ctx, "sp_typo_fail")["status"] == "active"
print("OK: an unknown referent reads OPEN in activate_when and CLOSED in fail_when (D2)")

# --- fail_when: fails an active thread and a not-yet-started one, and latches ----------------
ctx = make_ctx({
    "sp_live": {"title": "Live", "description": "l", "starts_active": True,
                "fail_when": {"flag": "lark_departed"}},
    "sp_later": {"title": "Later", "description": "l",
                 "activate_when": {"turn_gte": 1},
                 "fail_when": {"flag": "lark_departed"}},
    "sp_done": {"title": "Done", "description": "d", "starts_active": True,
                "fail_when": {"flag": "lark_departed"}},
}, flags_declared=["lark_departed"])
record(ctx, "sp_done").update(status="completed", active=False, progress=100)
out = se.apply_thread_conditions(ctx)
assert out["failed"] == [], out

ctx["state"]["protagonist"]["flags"]["active"]["lark_departed"] = True
ctx["state"]["pacing"]["turn_count"] = 5
out = se.apply_thread_conditions(ctx)
assert sorted(out["failed"]) == ["sp_later", "sp_live"], out
# Failure runs before activation: sp_later's activate_when holds too, but it fails instead.
assert out["activated"] == [], out
for sid in ("sp_live", "sp_later"):
    assert record(ctx, sid)["status"] == "failed" and record(ctx, sid)["active"] is False
# A completed thread is over and stays completed.
assert record(ctx, "sp_done")["status"] == "completed"

del ctx["state"]["protagonist"]["flags"]["active"]["lark_departed"]
out = se.apply_thread_conditions(ctx)
assert out == {"failed": [], "activated": []}, out
assert record(ctx, "sp_live")["status"] == "failed"
print("OK: fail_when fails live and pending threads before they can start, and latches")

# --- the ending sequence: nothing new starts --------------------------------------------------
ctx = make_ctx({"sp_x": {"title": "X", "description": "x", "activate_when": {"turn_gte": 0}}})
ctx["state"]["plot"]["endgame"]["requested"] = True
assert se.apply_thread_conditions(ctx)["activated"] == []
ctx["state"]["plot"]["endgame"]["requested"] = False
assert se.apply_thread_conditions(ctx)["activated"] == ["sp_x"]
print("OK: no thread activates once the story is in its ending sequence")

# --- a failed thread is no longer live: the generator may refill its slot --------------------
ctx = make_ctx({
    "sp_1": {"title": "One", "description": "1", "starts_active": True,
             "fail_when": {"flag": "gone"}},
}, flags_declared=["gone"])
story = se.state_store.thaw(ctx["story"])
story["plot"].setdefault("pacing", {})["max_parallel_subplots"] = 1
ctx["story"] = se.state_store.freeze(story)
calls = []
se.call_llm_json = lambda prompt, **kw: calls.append(prompt) or {
    "title": "Refill", "description": "r", "priority": "low", "ties_to_main_plot": "",
    "span": "single_act", "new_character": None}
se.generate_new_subplot(ctx)
assert calls == [], "a full pool must not ask for a new subplot"
ctx["state"]["protagonist"]["flags"]["active"]["gone"] = True
se.apply_thread_conditions(ctx)
se.generate_new_subplot(ctx)
assert len(calls) == 1, "a failed thread must free its slot in the live pool"
print("OK: a failed thread stops counting toward the live subplot pool")

# --- the pacing nudge never invites a thread that is waiting on its condition ----------------
ctx = make_ctx({
    "sp_gated": {"title": "Gated Thread", "description": "g",
                 "activate_when": {"stat": "reach", "gte": 99}},
})
ctx["state"]["protagonist"].setdefault("stats", {})["reach"] = 0
nudge = se.generate_pacing_nudge(ctx)
assert "Gated Thread" not in nudge, nudge
ctx = make_ctx({"sp_manual": {"title": "Manual Thread", "description": "m"}})
nudge = se.generate_pacing_nudge(ctx)
assert "SUBPLOT OPPORTUNITY" in nudge and "Manual Thread" in nudge, nudge
print("OK: the pacing nudge offers ungated pending threads, never one gated by activate_when")

print("\nALL CHECKS PASSED: test_thread_conditions")
