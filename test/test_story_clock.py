"""CR-13, the story clock engine (backend/clock.py and its readers).

The spec's acceptance criteria, one by one:
  - absent module: with no `story_clock`, every prompt and counter behaves as it always did (P-2);
  - idle detection: a turn whose observations report nothing on the list is idle, any single item makes it
    not idle, a finale turn is never idle;
  - free streak: `free_idle_streak` idle turns in a row leave the story clock unchanged, the next advances it;
  - bound: story_clock >= turn_count - free_idle_streak * (idle runs), a run of k costs max(0, k - free);
  - push: fires once per exhausted streak, never when a pacing rule fired that turn, never in the finale;
  - lean forward: the options instruction carries the line exactly while the streak is exhausted;
  - two clocks: each reader uses the clock its column of the spec's table names.
Plus the failure the design has to survive: a clock parked on a multiple must not re-fire what keys on a value.

Run directly: python3 test/test_story_clock.py
"""
import copy
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _llm_stubs import RecordingLLM, load_story_engine  # noqa: E402

se = load_story_engine()
clock = se.clock
mechanics = se.mechanics

CLOCK = {"free_idle_streak": 3, "push_directive": "A knock at the hatch: someone who cannot wait."}
EMPTY = {"subplot_beats": {}, "flags_set": {}, "revelations": {"revealed": [], "eligible": []},
         "inventory": {"gained": [], "used": []}, "new_characters": [],
         "scene_update": {"location": "", "summary": "unchanged", "present_npcs": []}}
FLAGS = [{"id": "f1", "detect": "the event f1 happens"}, {"id": "f_dec", "detect": "x"}]


def make_ctx(user="clocker", story_clock=CLOCK, extra=None):
    ctx = se.state_store.load_state(user, se.state_store.DEFAULT_STORY_SLUG)
    story = se.state_store.thaw(ctx["story"])
    pacing = story["plot"].setdefault("pacing", {})
    pacing.pop("story_clock", None)
    if story_clock is not None:
        pacing["story_clock"] = copy.deepcopy(story_clock)
    story.setdefault("mechanics", {})["flags"] = {"declared": copy.deepcopy(FLAGS)}
    for key, value in (extra or {}).items():
        story["mechanics"][key] = copy.deepcopy(value)
    frozen = se.state_store.freeze(story)
    mechanics.validate(frozen)
    state = se.state_store.new_save_state(frozen, se.state_store.DEFAULT_STORY_SLUG)
    state["history"]["recent_turns"].append("Player: x\nNarrator: a scene")
    return {"story": frozen, "state": state}


def run_turn(ctx, diff=None):
    """One turn of the real update path with a scripted state-update reply."""
    original, real_save, real_text = se.call_llm_json, se.state_store.save_state, se.call_llm
    se.call_llm_json = lambda prompt, **kw: {**copy.deepcopy(EMPTY), **(diff or {})}
    se.call_llm = lambda prompt, **kw: "a summary"  # the summary rollover, on a long run
    se.state_store.save_state = lambda *a, **k: None  # the story is modified in memory, so it would not match the template on disk
    try:
        se.update_state_after_turn(ctx, "an action", "The scene unfolds.", "clocker", se.state_store.DEFAULT_STORY_SLUG)
    finally:
        se.call_llm_json, se.state_store.save_state, se.call_llm = original, real_save, real_text
    return ctx["state"]["pacing"]


def idle(ctx):
    return run_turn(ctx)


def moved(ctx, what="flag"):
    diff = {"flag": {"flags_set": {"f1": {"value": True}}}}.get(what, {})
    return run_turn(ctx, diff)


P = lambda ctx: ctx["state"]["pacing"]  # noqa: E731

# --- absent module: nothing changes ------------------------------------------------------------------------
plain = make_ctx(story_clock=None)
assert not clock.authored(plain) and "story_clock" not in P(plain) and "idle_streak" not in P(plain)
idle(plain)
idle(plain)
assert P(plain)["turn_count"] == 2 and "story_clock" not in P(plain) and clock.story_turn(plain) == 2 and clock.advanced(plain)
assert P(plain)["turns_since_act_check"] == 2
prompt = se.build_system_prompt(plain)
assert clock.LEAN_FORWARD not in prompt and CLOCK["push_directive"] not in prompt
print("OK: with no story_clock there are no counters, no clock reader changes, and no prompt text")

# --- the counters exist only when authored, and a save that predates the clock adopts one --------------------------
ctx = make_ctx()
assert (P(ctx)["story_clock"], P(ctx)["idle_streak"], P(ctx)["push_fired"]) == (0, 0, False)
old = make_ctx(story_clock=None)
P(old)["turn_count"] = 40
old["story"] = make_ctx()["story"]  # the story now authors a clock; the save does not have one yet
clock.ensure(old)
assert P(old)["story_clock"] == 40, "starts where the turn counter is, so nothing already played is re-timed"
print("OK: a new save is seeded, and an old save adopts the clock at its current turn")

# --- idle detection: every item on the list moves the story, and nothing else does --------------------------------------
SIGNAL_EVENTS = {
    "thread": {"type": "subplot_beat", "id": "s", "beat": "advanced"},
    "stat": {"type": "stat_event", "key": "k"}, "stat2": {"type": "stat_changes", "changes": {"a": 1}},
    "social": {"type": "social", "target": "X", "register": "r"},
    "item": {"type": "item_gained", "label": "l", "tags": []}, "item2": {"type": "item_used", "item": "l"},
    "leverage": {"type": "leverage_gained", "kind": "k", "label": "l"}, "leverage2": {"type": "leverage_spent", "label": "l"},
    "fragment": {"type": "revelation_revealed", "id": "r"}, "waypoint": {"type": "waypoint_hit", "key": "a.b"},
}
for name, event in SIGNAL_EVENTS.items():
    assert clock.signals([event], {}), f"{name} must make a turn non-idle"
for core in ("flag", "place", "character"):
    assert clock.signals([], {core: True}) == [core]
for name, event in {"touched beat": {"type": "subplot_beat", "id": "s", "beat": "touched"},
                    "pacing classification": {"type": "beat", "beat": "respite", "intensity": 2},
                    "an eligible fragment": {"type": "revelation_eligible", "id": "r"}}.items():
    assert clock.signals([event], {"flag": False, "place": False, "character": False}) == [], f"{name} is not movement"
assert clock.signals([], {}) == [] and clock.signals(None, None) == []
print("OK: every item on the spec's list makes a turn non-idle; a touched beat, a classification and an eligible fragment do not")

# through the real update path: what the model reported and the engine accepted
ctx = make_ctx()
idle(ctx)
assert (P(ctx)["story_clock"], P(ctx)["idle_streak"]) == (0, 1)
run_turn(ctx, {"flags_set": {"f1": {"value": True}}})
assert (P(ctx)["story_clock"], P(ctx)["idle_streak"]) == (1, 0)
ctx = make_ctx()
run_turn(ctx, {"flags_set": {"f_dec": {"value": False}}})
assert P(ctx)["idle_streak"] == 1, "a declared flag reported false is dropped, so it moved nothing"
ctx = make_ctx()
run_turn(ctx, {"new_characters": [{"name": "Marlowe Vance", "description": "d", "role": "r", "first_contact": "f", "hook": "h"}]})
assert P(ctx)["idle_streak"] == 0 and P(ctx)["story_clock"] == 1, "a newly named character is movement"
run_turn(ctx, {"new_characters": [{"name": "Marlowe Vance", "description": "d"}]})
assert P(ctx)["idle_streak"] == 1, "the same character again is not"
locations = list((se.state_store.thaw(ctx["story"])["world"].get("locations") or {}))
if len(locations) > 1:
    here = ctx["state"]["scene"]["location"]
    elsewhere = next(l for l in locations if l != here)
    run_turn(ctx, {"scene_update": {"location": elsewhere, "summary": "s", "present_npcs": []}})
    assert P(ctx)["idle_streak"] == 0, "a change of place is movement"
print("OK: the update path reads what was applied, not what was claimed")

# --- the finale is never idle; a failed observation pass never comes free -------------------------------------------------
ctx = make_ctx()
ctx["state"]["plot"]["endgame"]["requested"] = True
idle(ctx)
assert P(ctx)["idle_streak"] == 0 and P(ctx)["story_clock"] == 1
ctx = make_ctx()
def broken(prompt, **kw):
    raise ValueError("not json")
original, real_save = se.call_llm_json, se.state_store.save_state
se.call_llm_json = broken
se.state_store.save_state = lambda *a, **k: None
try:
    se.update_state_after_turn(ctx, "an action", "The scene unfolds.", "clocker", se.state_store.DEFAULT_STORY_SLUG)
finally:
    se.call_llm_json, se.state_store.save_state = original, real_save
assert P(ctx)["story_clock"] == 1 and P(ctx)["idle_streak"] == 0, "an unobserved turn counts as moving"
print("OK: a finale turn is never idle, and a state-update pass that failed does not make a turn free")

# --- the free streak and its bound -----------------------------------------------------------------------------------------------
ctx = make_ctx()
for k in range(1, 4):
    idle(ctx)
    assert (P(ctx)["story_clock"], P(ctx)["idle_streak"]) == (0, k), f"idle turn {k} is free"
idle(ctx)
assert (P(ctx)["story_clock"], P(ctx)["idle_streak"]) == (1, 4), "the next one advances it"
idle(ctx)
assert P(ctx)["story_clock"] == 2, "and every one after"
moved(ctx)
assert (P(ctx)["story_clock"], P(ctx)["idle_streak"]) == (3, 0)

import random  # noqa: E402
rng = random.Random(13)
ctx = make_ctx()
runs, current = 0, 0
for _ in range(120):
    if rng.random() < 0.6:
        idle(ctx)
        current += 1
    else:
        moved(ctx)
        runs += 1 if current else 0
        current = 0
runs += 1 if current else 0
turn, story = P(ctx)["turn_count"], P(ctx)["story_clock"]
assert story >= turn - CLOCK["free_idle_streak"] * runs, (story, turn, runs)
assert turn - story <= CLOCK["free_idle_streak"] * runs
print(f"OK: {CLOCK['free_idle_streak']} free idle turns, the next advances, and story_clock >= turn_count - free x runs "
      f"({story} >= {turn} - {CLOCK['free_idle_streak']} x {runs} over 120 random turns)")

# --- lean forward: exactly while the streak is exhausted ---------------------------------------------------------------------------------
ctx = make_ctx()
def leans(c):
    return clock.LEAN_FORWARD in se.build_system_prompt(c)
assert not leans(ctx)
for _ in range(2):
    idle(ctx)
assert not leans(ctx), "two idle turns of three: not yet"
idle(ctx)
assert leans(ctx) and clock.exhausted(ctx), "used up: the next prompt leans forward"
idle(ctx)
assert leans(ctx), "and for as long as the streak holds"
moved(ctx)
assert not leans(ctx), "an action ends it"
ctx["state"]["plot"]["endgame"]["requested"] = True
P(ctx)["idle_streak"] = 9
assert not leans(ctx) and not clock.exhausted(ctx), "never in the finale (which asks for no options)"
print("OK: the options instruction leans forward exactly while the free streak is exhausted")

# --- the push ---------------------------------------------------------------------------------------------------------------------------------
ctx = make_ctx()
for _ in range(3):
    idle(ctx)
first = se.build_system_prompt(ctx)
assert f"PACING DIRECTIVE: {CLOCK['push_directive']}" in first and P(ctx)["push_fired"] is True
assert CLOCK["push_directive"] not in se.build_system_prompt(ctx), "once for this streak"
idle(ctx)
assert CLOCK["push_directive"] not in se.build_system_prompt(ctx), "not again while the same streak runs on"
moved(ctx)
assert P(ctx)["push_fired"] is False, "a turn that moves the story re-arms it"
for _ in range(3):
    idle(ctx)
assert CLOCK["push_directive"] in se.build_system_prompt(ctx), "used up again: it fires again"
print("OK: the push fires once per exhausted streak and re-arms when the streak resets")

# a story with a clock but no push: the options still lean, nothing else is added
ctx = make_ctx(story_clock={"free_idle_streak": 2})
for _ in range(2):
    idle(ctx)
prompt = se.build_system_prompt(ctx)
assert clock.LEAN_FORWARD in prompt and "PACING DIRECTIVE" not in prompt
print("OK: without a push_directive only the options lean forward")

# a pacing rule that fires the same turn wins, and the push is spent for the streak all the same
ctx = make_ctx()
loop = mechanics.bound_for(ctx["story"], "pacing_loop")
rule_id = loop.engine.rule(loop.cfg)["id"]
for _ in range(3):
    idle(ctx)
P(ctx)["armed"] = {rule_id: {"deferrals": 0}}
prompt = se.build_system_prompt(ctx)
assert "PACING DIRECTIVE" in prompt and CLOCK["push_directive"] not in prompt, "the armed rule's directive is used, the push is not"
assert P(ctx)["push_fired"] is True
P(ctx)["armed"] = {}
assert CLOCK["push_directive"] not in se.build_system_prompt(ctx), "and it is spent: it does not fire next turn as if new"
print("OK: an armed pacing rule takes the turn and the push is spent for that streak")

# never in the finale
ctx = make_ctx()
for _ in range(3):
    idle(ctx)
ctx["state"]["plot"]["endgame"]["requested"] = True
assert CLOCK["push_directive"] not in se.build_system_prompt(ctx)
print("OK: no push in the finale")

# --- two clocks: each reader on the clock its column names ------------------------------------------------------------------------------------
ENDINGS = {"engine": "ending_funnel", "check_every": 3, "budget": {"open_until": 6, "narrow_until": 12, "commit_by": 24},
           "entries": [{"id": "a", "kind": "destination", "name": "A", "ready_when": {"flag": "f1"},
                        "arc": {"title": "A", "description": "d"}, "waypoints": []},
                       {"id": "b", "kind": "destination", "name": "B", "arc": {"title": "B", "description": "d"}, "waypoints": []}]}
ctx = make_ctx(extra={"endings": ENDINGS})
cfg = mechanics.bound_for(ctx["story"], "endings")
for _ in range(3):
    idle(ctx)
assert P(ctx)["turn_count"] == 3 and P(ctx)["story_clock"] == 0
assert cfg.engine.turn(ctx) == 0 and cfg.engine.phase(cfg.cfg, ctx) == "open", "the budget reads the story clock"
for _ in range(10):
    idle(ctx)
assert P(ctx)["turn_count"] == 13 and P(ctx)["story_clock"] == 10
assert cfg.engine.phase(cfg.cfg, ctx) == "narrow", "10 on the clock is past open_until 6 and short of narrow_until 12, whatever the 13 played say"
print("OK: the ending budget's phase follows the story clock, not the turn count")

# turn_gte reads the story clock
ctx = make_ctx()
for _ in range(3):
    idle(ctx)
P(ctx)["turn_count"] = 50  # 50 exchanges played...
assert P(ctx)["story_clock"] == 0
import conditions  # noqa: E402
assert not conditions.satisfied({"turn_gte": 10}, ctx, conditions.CLOSED), "...but only 0 turns of story"
P(ctx)["story_clock"] = 12
assert conditions.satisfied({"turn_gte": 10}, ctx, conditions.CLOSED)
plain = make_ctx(story_clock=None)
P(plain)["turn_count"] = 12
assert conditions.satisfied({"turn_gte": 10}, plain, conditions.CLOSED), "no clock: turn_count, as ever"
print("OK: turn_gte reads the story clock when there is one")

# the nudge cadence and the act cadence
ctx = make_ctx()
for _ in range(3):
    idle(ctx)
assert P(ctx)["turns_since_nudge"] == 3, "the nudge stays on turn_count: a player stuck asking questions is who needs it"
assert P(ctx)["turns_since_act_check"] == 0, "a free idle turn does not bring the act director's next check closer"
idle(ctx)
assert P(ctx)["turns_since_act_check"] == 1
print("OK: nudges count every turn; the act cadence counts only turns the clock moved")

# --- a parked clock must not re-fire what keys on a value -----------------------------------------------------------------------------------------------
# a funnel check is `clock % check_every == 0`; park the clock on a multiple and idle turns must not re-run it
ctx = make_ctx(extra={"endings": ENDINGS})
bound = mechanics.bound_for(ctx["story"], "endings")
for _ in range(3):
    moved(ctx)  # the clock reaches 3, a check turn
    assert bound.engine.is_check_turn(bound.cfg, ctx) == (P(ctx)["story_clock"] % 3 == 0)
assert P(ctx)["story_clock"] == 3 and bound.engine.is_check_turn(bound.cfg, ctx)
idle(ctx)
assert P(ctx)["story_clock"] == 3 and not bound.engine.is_check_turn(bound.cfg, ctx), \
    "still 3 on the clock, but it did not move this turn: not a check"
moved(ctx)
assert P(ctx)["story_clock"] == 4 and not bound.engine.is_check_turn(bound.cfg, ctx)
print("OK: a funnel check runs when the clock reaches a multiple, not on every idle turn that leaves it there")

# ...the same for stat drift: a per_turn axis with an interval, on a parked clock
STATS = {"engine": "bounded_counter", "floor": 0, "ceiling": 100, "axes": {"deadline": {"per_turn": -1, "per_turn_interval": 3}}}
ctx = make_ctx(extra={"stats": STATS})
ctx["state"]["protagonist"]["stats"] = {"deadline": 50}
for _ in range(3):
    moved(ctx)
assert P(ctx)["story_clock"] == 3 and ctx["state"]["protagonist"]["stats"]["deadline"] == 49, "ticked once, at clock 3"
for _ in range(2):
    idle(ctx)
assert P(ctx)["story_clock"] == 3 and ctx["state"]["protagonist"]["stats"]["deadline"] == 49, \
    "the clock is parked on 3 and must not tick the drift again"
for _ in range(4):
    moved(ctx)
assert P(ctx)["story_clock"] == 7 and ctx["state"]["protagonist"]["stats"]["deadline"] == 48, "next tick at 6"
plain = make_ctx(story_clock=None, extra={"stats": STATS})
plain["state"]["protagonist"]["stats"] = {"deadline": 50}
for _ in range(6):
    idle(plain)
assert plain["state"]["protagonist"]["stats"]["deadline"] == 48, "no clock: every turn counts, as it always did"
print("OK: stat drift runs on the story clock, once per interval, and never again while the clock is parked")

# --- the finale's own length stays on turn_count ------------------------------------------------------------------------------------------------------------
ctx = make_ctx()
se._begin_endgame(ctx, {"title": "T", "description": "D"}, cause="forced")
before = ctx["state"]["plot"]["endgame"]["requested_turn"]
for _ in range(2):
    idle(ctx)
assert ctx["state"]["pacing"]["turn_count"] - before == 2, "the finale counts every exchange: it never comes free"
print("OK: a finale turn counts (turn_count) and is never idle")

# --- regenerate: the counters are state, so a re-roll restores them --------------------------------------------------------------------------------------------
ctx = make_ctx()
for _ in range(2):
    idle(ctx)
snapshot = copy.deepcopy(ctx["state"])
idle(ctx)
assert (P(ctx)["idle_streak"], snapshot["pacing"]["idle_streak"]) == (3, 2)
restored = {"story": ctx["story"], "state": snapshot}
idle(restored)
assert P(restored)["idle_streak"] == 3 and P(restored)["story_clock"] == P(ctx)["story_clock"], "the re-roll decides the same"
print("OK: a restored snapshot re-decides the turn identically")

# --- the trace records it ----------------------------------------------------------------------------------------------------------------------------------------------
USER, SLUG = "clocktrace", "clock_trace_story"
engine_trace = se.engine_trace
ctx = make_ctx(user=USER)
ctx["state"]["story_slug"] = SLUG
path = engine_trace.path_for(USER, SLUG)
if os.path.exists(path):
    os.remove(path)
engine_trace.bind(USER, SLUG)
engine_trace.ensure_run(ctx)
try:
    for turn in range(1, 8):
        (moved if turn == 5 else idle)(ctx)
        se.build_system_prompt(ctx)
        engine_trace.flush_turn(ctx, **se._trace_turn_fields(ctx))
finally:
    engine_trace.unbind()
events = [json.loads(line) for line in open(path, encoding="utf-8")]
clocks = [e for e in events if e["kind"] == "clock"]
assert [(e["idle"], e["free"], e["step"]) for e in clocks] == [
    (True, True, 0), (True, True, 0), (True, True, 0), (True, False, 1), (False, False, 1), (True, True, 0), (True, True, 0)]
assert clocks[4]["signals"] == ["flag"] and clocks[3]["streak"] == 4
turns = [e for e in events if e["kind"] == "turn"]
assert [t["story_clock"] for t in turns] == [0, 0, 0, 1, 2, 2, 2] and turns[2]["lean_forward"] is True and turns[4]["lean_forward"] is False
assert [e["fired"] for e in events if e["kind"] == "push"] == [True]
start = next(e for e in events if e["kind"] == "run_start")
assert start["pacing"]["free_idle_streak"] == 3 and start["pacing"]["has_push"] is True and start["features"]["story_clock"] is True
print("OK: every clock decision, its signals, the push and the lean-forward turns are in the trace")

print("\nALL CHECKS PASSED: test_story_clock")
