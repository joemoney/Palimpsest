"""Steering, second slice (AUTHORING_TOOL_PHASES.md S5): what reaches the narrator through the pacing nudge.

CR-10's carrier priority (a running thread that carries an unplanted waypoint of a steered destination is raised
for the nudge and its line carries the waypoint's `plant`), CR-05's `hint` (one steered destination's fragment,
at most one per nudge, rotating) and the drive nudge (from `narrow_until`, the leader's missing waypoints as the
scene's priority), which yields to a pacing-loop rule that is about to fire.

Covers what goes in, what must never go in (an ending's name, arc, criteria, `detect`), rotation, the phase
gates, that a story without steering builds exactly the nudge it always did, that a dry run of the directive
decision changes no state, and that the trace records each of it.

Run directly: python3 test/test_nudge_steering.py
"""
import copy
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _llm_stubs import load_story_engine  # noqa: E402

se = load_story_engine()
mechanics = se.mechanics
ENGINE = mechanics.endings.ENGINE
engine_trace = se.engine_trace

ENDINGS = {
    "engine": "ending_funnel", "check_every": 3,
    "budget": {"open_until": 6, "narrow_until": 12, "commit_by": 24}, "steer_top": 2,
    "entries": [
        {"id": "alpha", "kind": "destination", "name": "SECRET ALPHA NAME",
         "viable_while": {"not": {"flag": "alpha_dead"}}, "hint": "the ship is quieter than it should be",
         "criteria": "SECRET CRITERIA", "arc": {"title": "SECRET ARC", "description": "SECRET ARC BODY"},
         "waypoints": [
             {"id": "a1", "plant": "the lamp gutters", "done_when": {"flag": "a1_done"}},
             {"id": "a2", "plant": "a door answers unasked", "detect": "SECRET DETECT A2", "done_when": {"flag": "a2_done"}},
             {"id": "a3", "plant": "the logbook is missing a page", "done_when": {"flag": "a3_done"}}]},
        {"id": "beta", "kind": "destination", "name": "SECRET BETA NAME", "hint": "someone hums in the corridor",
         "waypoints": [{"id": "b1", "plant": "the licence is questioned", "done_when": {"flag": "b1_done"}}]},
        {"id": "gamma", "kind": "destination", "name": "SECRET GAMMA NAME",
         "viable_while": {"not": {"flag": "gamma_dead"}},
         "waypoints": [{"id": "g1", "plant": "a stranger asks for the ship", "done_when": {"flag": "g1_done"}}]},
    ],
}
BASE = {"completion_threshold": 99, "description": "d", "role": "spine"}
SUBPLOTS = {
    "s_first": dict(BASE, title="First Thread", starts_active=True, priority="high"),
    "s_carrier": dict(BASE, title="Carrier Thread", starts_active=True, priority="medium", delivers=["alpha.a1", "alpha.a2"]),
    "s_beta": dict(BASE, title="Beta Thread", starts_active=True, priority="low", delivers=["beta.b1"]),
    "s_dormant": dict(BASE, title="Dormant Thread", activate_when={"turn_gte": 9999}, priority="low", delivers=["gamma.g1"]),
}
FLAGS = [{"id": f, "detect": f"the event {f} happens"} for f in
         ("a1_done", "a2_done", "a3_done", "b1_done", "g1_done", "alpha_dead", "gamma_dead")]
SECRETS = ("SECRET", "alpha", "beta ", "gamma", "criteria", "a1_done")


def make_ctx(user="nudger", endings=ENDINGS, subplots=SUBPLOTS, with_endings=True):
    ctx = se.state_store.load_state(user, se.state_store.DEFAULT_STORY_SLUG)
    story = se.state_store.thaw(ctx["story"])
    story.setdefault("mechanics", {})
    if with_endings:
        story["mechanics"]["endings"] = copy.deepcopy(endings)
        story["mechanics"]["flags"] = {"declared": copy.deepcopy(FLAGS)}
    story["plot"]["subplots"] = copy.deepcopy(subplots)
    frozen = se.state_store.freeze(story)
    mechanics.validate(frozen)
    state = se.state_store.new_save_state(frozen, se.state_store.DEFAULT_STORY_SLUG)
    state["history"]["recent_turns"].append("Player: x\nNarrator: a scene")
    return {"story": frozen, "state": state}


def at(ctx, turn):
    ctx["state"]["pacing"]["turn_count"] = turn


def nudge(ctx):
    report = {}
    return se.generate_pacing_nudge(ctx, report), report


def bucket(ctx):
    return ctx["state"]["mechanics"]["endings"]


cfg = None

# --- a story with no steering builds exactly the nudge it always did (P-2) -------------------------------------------
plain = make_ctx(with_endings=False)
text, rep = nudge(plain)
assert "SET UP" not in text and "DETAIL TO WORK IN" not in text and "PRIORITY THIS SCENE" not in text
assert rep["steering_chars"] == 0 and rep["mode"] == "normal" and rep["carrier_plants"] == [] and rep["hint"] is None
bare = make_ctx(endings={**ENDINGS, "entries": [{"id": "only", "kind": "destination", "name": "Only",
                                                  "waypoints": [{"id": "w", "plant": "x", "done_when": {"flag": "a1_done"}}]}]})
at(bare, 3)
assert nudge(bare)[0] == nudge(plain)[0], "endings with no hint and no carrier add nothing to the nudge"
print("OK: no endings, or endings with nothing to say, leave the nudge byte for byte as it was")

# --- carrier priority and the plant on the thread's line ------------------------------------------------------------------
ctx = make_ctx()
cfg = mechanics.bound_for(ctx["story"], "endings").cfg
at(ctx, 3)
text, rep = nudge(ctx)
assert rep["primary_unboosted"] == "s_first" and rep["primary"] == "s_carrier", rep
assert "ACTIVE SUBPLOT: 'Carrier Thread'" in text, "a medium carrier ties a high thread and wins the tie: raised for this nudge"
assert "SET UP THROUGH THIS THREAD: the lamp gutters" in text
assert "(set up: the licence is questioned)" in text and "First Thread" in text.split("BACKGROUND SUBPLOTS:")[1]
assert [p["sid"] for p in rep["carrier_plants"]] == ["s_carrier", "s_beta"] and rep["boosted"] == ["s_carrier", "s_beta"]
assert len(rep["carrier_plants"]) <= mechanics.endings.MAX_PLANTS == 2
assert not any(w in text for w in SECRETS[:4]) and "SECRET DETECT" not in text and "the event a1_done" not in text
assert "the ship is quieter" in text, "one steered destination's hint is worked in"
assert text.count("A DETAIL TO WORK IN") == 1
print("OK: a running carrier is raised, its line carries the plant, and no ending name, arc, criteria or detect appears")

# a dormant carrier (gamma) is never named: only running threads carry a plant
assert "a stranger asks for the ship" not in text
print("OK: a dormant thread is not asked to carry anything")

# --- rotation: what a nudge named is not the first choice next time ------------------------------------------------------------
assert bucket(ctx)["nudge_offers"] == {"alpha.a1": 1, "beta.b1": 1}
assert bucket(ctx)["hints_shown"] == {"alpha": 1}
text2, rep2 = nudge(ctx)
assert "SET UP THROUGH THIS THREAD: a door answers unasked" in text2, "the carrier's other waypoint takes its turn"
assert rep2["hint"]["dest"] == "beta" and "someone hums in the corridor" in text2, "the hint rotates to the next destination"
assert bucket(ctx)["hints_shown"] == {"alpha": 1, "beta": 1}
text3, rep3 = nudge(ctx)
assert rep3["hint"]["dest"] == "alpha", "and back round, never two hints in one nudge"
print("OK: plants and hints rotate, one hint per nudge")

# planted waypoints, pruned and committed destinations offer nothing
ctx = make_ctx()
at(ctx, 3)
bucket(ctx)["waypoints_done"] = {"alpha.a1": 2, "alpha.a2": 2, "alpha.a3": 2}
bucket(ctx)["pruned"] = {"beta": 1}
text, rep = nudge(ctx)
assert rep["carrier_plants"] == [] and "SET UP" not in text and "set up:" not in text, rep
assert rep["hint"] is not None and rep["hint"]["dest"] == "alpha", "alpha still steered, its hint stays available; pruned beta's does not"
bucket(ctx)["committed"] = {"id": "alpha", "turn": 3, "forced": False}
text, rep = nudge(ctx)
assert rep["hint"] is None and rep["carrier_plants"] == [] and rep["drive"] is None
print("OK: planted waypoints, pruned destinations and a committed ending add nothing")

# --- the drive nudge: from narrow_until, the leader's missing waypoints --------------------------------------------------------
ctx = make_ctx()
for turn in (3, 9):
    at(ctx, turn)
    assert nudge(ctx)[1]["drive"] is None, f"no drive nudge before narrow_until (turn {turn})"
at(ctx, 13)
bucket(ctx)["scores"] = {"alpha": 0.6, "beta": 0.2, "gamma": 0.1}
text, rep = nudge(ctx)
assert rep["mode"] == "drive" and rep["drive"]["leader"] == "alpha" and rep["drive"]["keys"] == ["alpha.a1", "alpha.a2"], rep
assert "PRIORITY THIS SCENE: the lamp gutters (through 'Carrier Thread'); a door answers unasked (through 'Carrier Thread')." in text
assert "the logbook is missing a page" not in text, "at most two"
assert not any(w in text for w in ("SECRET", "alpha", "gamma", "SECRET DETECT", "a2_done")), text
bucket(ctx)["scores"] = {"alpha": 0.1, "beta": 0.9, "gamma": 0.1}
text, rep = nudge(ctx)
assert rep["drive"]["leader"] == "beta" and "the licence is questioned (through 'Beta Thread')" in text
print("OK: from narrow_until the nudge names the leader's missing waypoints (at most two, with the thread that can deliver each)")

# --- the drive nudge yields to a pacing rule that is about to fire, and only then -----------------------------------------------
ctx = make_ctx()
at(ctx, 13)
bucket(ctx)["scores"] = {"alpha": 0.6}
loop = mechanics.bound_for(ctx["story"], "pacing_loop")
rule_id = loop.engine.rule(loop.cfg)["id"]
ctx["state"]["pacing"]["armed"] = {rule_id: {"deferrals": 0}}
before_state = json.dumps(ctx["state"]["pacing"], sort_keys=True)
assert se._pacing_directive_will_fire(ctx) == rule_id
assert json.dumps(ctx["state"]["pacing"], sort_keys=True) == before_state, "the dry run pops nothing and counts no deferral"
text, rep = nudge(ctx)
assert rep["drive_yielded_to"] == rule_id and rep["drive"] is None and rep["mode"] == "normal" and "PRIORITY THIS SCENE" not in text
ctx["state"]["pacing"]["last_fired_rule"] = rule_id  # just_fired suppresses the rule this turn
assert se._pacing_directive_will_fire(ctx) is None, "example's rule is suppressed for the turn after it fires (just_fired)"
text, rep = nudge(ctx)
assert rep["mode"] == "drive" and rep["drive_yielded_to"] is None and "PRIORITY THIS SCENE" in text, \
    "a rule that is deferred this turn does not have the floor, so the drive nudge stays"
ctx["state"]["pacing"]["armed"] = {}
assert se._pacing_directive_will_fire(ctx) is None and nudge(ctx)[1]["mode"] == "drive"
print("OK: an armed rule about to fire has the floor and the drive nudge yields; a deferred or unarmed one does not")

# --- the whole thing through the real prompt build, and the trace records it ------------------------------------------------------
ctx = make_ctx()
ctx["state"]["story_slug"] = "nudge_trace"
at(ctx, 13)
bucket(ctx)["scores"] = {"alpha": 0.6}
ctx["state"]["pacing"]["turns_since_nudge"] = 99
engine_trace.bind("nudger", "nudge_trace")
path = engine_trace.path_for("nudger", "nudge_trace")
if os.path.exists(path):
    os.remove(path)
prompt = se.build_system_prompt(ctx)
assert "PRIORITY THIS SCENE" in prompt and "SET UP THROUGH THIS THREAD" in prompt and "A DETAIL TO WORK IN" in prompt
engine_trace.flush_turn(ctx)
engine_trace.unbind()
events = [json.loads(line) for line in open(path, encoding="utf-8")]
(ev,) = [e for e in events if e["kind"] == "nudge"]
assert ev["mode"] == "drive" and ev["drive"]["leader"] == "alpha" and ev["hint"] and ev["carrier_plants"]
assert ev["steering_chars"] > 0 and ev["chars"] >= ev["steering_chars"] and ev["boosted"] and ev["turn"] == 13
assert "SECRET" not in json.dumps(ev)
assert ctx["state"]["pacing"]["turns_since_nudge"] == 0
print("OK: a built nudge writes one trace event with what steering added and how large it was")

# --- regenerate safety: the counters are state, so a restored snapshot rotates the same way ---------------------------------------
ctx = make_ctx()
at(ctx, 3)
snapshot = copy.deepcopy(ctx["state"])
first_text, _ = nudge(ctx)
restored = {"story": ctx["story"], "state": snapshot}
assert nudge(restored)[0] == first_text, "re-rolling a turn rebuilds the same nudge, not the next one in the rotation"
print("OK: a restored snapshot rebuilds the same nudge")

print("\nALL CHECKS PASSED: test_nudge_steering")
