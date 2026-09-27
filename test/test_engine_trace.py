"""The engine trace (backend/engine_trace.py): the properties that make it safe to leave on and trustworthy
to count from - one line per event under a stable schema, bounded, per-run, never able to fail a turn, off
by switch, silent when muted, and the regen and run-id conventions the report relies on.

Run directly: python3 test/test_engine_trace.py
"""
import json
import os
import stat
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _llm_stubs import load_story_engine  # noqa: E402

se = load_story_engine()
engine_trace = se.engine_trace


def fresh_ctx(slug="trace_unit"):
    return {"story": {"story_version": "t.1", "plot": {}}, "state": {"story_slug": slug, "pacing": {"turn_count": 7},
                                                                       "plot": {"current_act": 2}}}


def read(user, story):
    with open(engine_trace.path_for(user, story), encoding="utf-8") as f:
        return [json.loads(line) for line in f]


# --- one line per event, stable fields -----------------------------------------------------------------------
engine_trace.bind("u1", "story_a")
ctx = fresh_ctx("story_a")
engine_trace.emit(ctx, "thing", a=1, b=[1, 2], c={"x": "y"})
engine_trace.emit(ctx, "thing", a=2)
rows = read("u1", "story_a")
assert [r["a"] for r in rows] == [1, 2]
for r in rows:
    assert {"v", "ts", "run", "user", "story", "turn", "act", "kind", "regen"} <= set(r), r
    assert r["v"] == 1 and r["turn"] == 7 and r["act"] == 2 and r["user"] == "u1" and r["story"] == "story_a" and r["regen"] is False
print("OK: one JSON line per event with a stable envelope (turn, act, run, user, story, regen)")

# --- bounded: strings clipped, collections capped, no prompt-sized payloads --------------------------------------
engine_trace.emit(ctx, "big", text="x" * 5000, items=list(range(500)), many={str(i): i for i in range(500)}, f=1.234567891)
big = read("u1", "story_a")[-1]
assert len(big["text"]) <= 240 and len(big["items"]) == 60 and len(big["many"]) == 60 and big["f"] == 1.2346
assert len(json.dumps(big)) < 4000, "a line stays under a page, so concurrent appends do not interleave"
print("OK: strings are clipped and collections capped")

# --- run id: minted once, stored in the state, kept across calls -----------------------------------------------------
ctx = fresh_ctx("story_b")
engine_trace.bind("u1", "story_b")
first = engine_trace.ensure_run(ctx)
assert first and ctx["state"]["run_id"] == first and engine_trace.ensure_run(ctx) == first
rows = read("u1", "story_b")
assert [r["kind"] for r in rows] == ["run_start"] and rows[0]["run"] == first and rows[0]["build"] == engine_trace.TRACE_BUILD
assert set(rows[0]["features"]) >= {"flags_detect", "plant", "early_activation"}
print("OK: a run id is minted once per save and a run_start records the build and its features")

# --- turn accumulator: timings and prompt sizes flush as one turn event; deferred events carry the turn -------------
engine_trace.bind("u1", "story_c")
ctx = fresh_ctx("story_c")
engine_trace.note_timing("narration", 2.0)
engine_trace.note_timing("narration", 1.0)
engine_trace.note_timing("state_update", 0.5)
engine_trace.note_prompt("narration", 1000)
engine_trace.note_prompt("narration", 500)
engine_trace.defer(ctx, "nudge", chars=42)
ctx["state"]["pacing"]["turn_count"] = 8  # the counter moves after the prompt was built
engine_trace.flush_turn(ctx, phase="open")
rows = read("u1", "story_c")
assert [r["kind"] for r in rows] == ["nudge", "turn"] and all(r["turn"] == 8 for r in rows), rows
assert rows[1]["timings"] == {"narration": {"n": 2, "s": 3.0}, "state_update": {"n": 1, "s": 0.5}}
assert rows[1]["prompt_chars"] == {"narration": 1500} and rows[1]["phase"] == "open"
engine_trace.flush_turn(ctx)
assert read("u1", "story_c")[-1]["timings"] == {}, "the accumulator resets after a flush"
print("OK: timings and prompt sizes flush once per turn, and an event raised mid-prompt carries the turn's own number")

# --- regen flag ----------------------------------------------------------------------------------------------------------
engine_trace.bind("u1", "story_d", regen=True)
engine_trace.emit(fresh_ctx("story_d"), "x")
assert read("u1", "story_d")[0]["regen"] is True
print("OK: events written while re-rolling a turn are marked regen")

# --- muted, and the off switch ---------------------------------------------------------------------------------------------
engine_trace.bind("u1", "story_e")
with engine_trace.muted():
    engine_trace.emit(fresh_ctx("story_e"), "x")
    engine_trace.defer(fresh_ctx("story_e"), "y")
assert not os.path.exists(engine_trace.path_for("u1", "story_e"))
os.environ["PALIMPSEST_TRACE"] = "0"
try:
    engine_trace.emit(fresh_ctx("story_e"), "x")
    assert not os.path.exists(engine_trace.path_for("u1", "story_e"))
finally:
    del os.environ["PALIMPSEST_TRACE"]
print("OK: muted and PALIMPSEST_TRACE=0 write nothing")

# --- a broken trace never reaches the turn ------------------------------------------------------------------------------------
engine_trace.bind("u1", "story_f")
os.makedirs(os.path.dirname(engine_trace.path_for("u1", "story_f")), exist_ok=True)
with open(engine_trace.path_for("u1", "story_f"), "w") as f:
    f.write("")
os.chmod(engine_trace.path_for("u1", "story_f"), stat.S_IRUSR)
try:
    if os.geteuid() != 0:  # root ignores file modes, so the write would simply succeed
        engine_trace.emit(fresh_ctx("story_f"), "x")
        assert engine_trace._warned, "a failed write is noted once and tracing stops for the process"
finally:
    os.chmod(engine_trace.path_for("u1", "story_f"), stat.S_IRUSR | stat.S_IWUSR)
    engine_trace._warned = False
class Exploding:
    def __getitem__(self, k):
        raise RuntimeError("boom")
engine_trace.emit(Exploding(), "x")  # a ctx that cannot be read must not raise
engine_trace._warned = False
print("OK: an unwritable file or an unreadable ctx costs the trace, never the turn")

# --- the trace changes no game state beyond the run id ---------------------------------------------------------------------------
ctx = fresh_ctx("story_g")
before = json.dumps(ctx, sort_keys=True)
engine_trace.bind("u1", "story_g")
engine_trace.emit(ctx, "x")
assert json.dumps(ctx, sort_keys=True) == before
print("OK: emitting changes no state")

print("\nALL CHECKS PASSED: test_engine_trace")
