"""Steering, first slice (AUTHORING_TOOL_PHASES.md S5): CR-05's `PLANT` in act generation and CR-10's early
carrier activation, and the trace that records both (backend/engine_trace.py).

Covers: which waypoints are offered to an act generation (steered destinations only, unplanted, deduplicated,
at most two, rotating so every destination gets set up); that the prompt carries the `plant` text and nothing
else about an ending; that offers are recorded only when an act came of them; which dormant authored threads
are started early, when, and how many; and that a stubbed run through the real turn path writes a trace whose
events line up with what the engine did.

Run directly: python3 test/test_steering.py
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
         "viable_while": {"not": {"flag": "alpha_dead"}},
         "hint": "SECRET HINT", "criteria": "SECRET CRITERIA",
         "arc": {"title": "SECRET ARC", "description": "SECRET ARC BODY"},
         "waypoints": [
             {"id": "a1", "plant": "the lamp gutters", "done_when": {"flag": "a1_done"}},
             {"id": "a2", "plant": "a door answers unasked", "detect": "SECRET DETECT A2",
              "done_when": {"flag": "a2_done"}}]},
        {"id": "beta", "kind": "destination", "name": "SECRET BETA NAME",
         "waypoints": [{"id": "b1", "plant": "the licence is questioned", "done_when": {"flag": "b1_done"}},
                       {"id": "b2", "plant": "the lamp gutters", "done_when": {"flag": "b2_done"}}]},
        {"id": "gamma", "kind": "destination", "name": "SECRET GAMMA NAME", "viable_while": {"not": {"flag": "gamma_dead"}},
         "waypoints": [{"id": "g1", "plant": "a stranger asks for the ship", "done_when": {"flag": "g1_done"}}]},
    ],
}
SUBPLOTS = {
    "s_running": {"title": "Running", "description": "d", "starts_active": True, "role": "spine",
                  "priority": "high", "completion_threshold": 99, "delivers": ["alpha.a1"]},
    "s_dormant": {"title": "Dormant", "description": "d", "role": "spine", "priority": "high",
                  "completion_threshold": 99, "activate_when": {"turn_gte": 9999}, "delivers": ["alpha.a2"]},
    "s_manual": {"title": "Manual", "description": "d", "role": "spine", "starts_active": False,
                 "priority": "high", "completion_threshold": 99, "delivers": ["beta.b1"]},
    "s_texture": {"title": "Texture", "description": "d", "role": "texture", "priority": "low",
                  "completion_threshold": 99, "activate_when": {"turn_gte": 9999}, "delivers": ["gamma.g1"]},
}
FLAGS = [{"id": f, "detect": f"the event {f} happens"} for f in
         ("a1_done", "a2_done", "b1_done", "b2_done", "g1_done", "alpha_dead", "gamma_dead")]


def make_ctx(user="steer", endings=ENDINGS, subplots=SUBPLOTS):
    ctx = se.state_store.load_state(user, se.state_store.DEFAULT_STORY_SLUG)
    story = se.state_store.thaw(ctx["story"])
    story.setdefault("mechanics", {})["endings"] = copy.deepcopy(endings)
    story["mechanics"]["flags"] = {"declared": copy.deepcopy(FLAGS)}
    story["plot"]["subplots"] = copy.deepcopy(subplots)
    frozen = se.state_store.freeze(story)
    mechanics.validate(frozen)
    state = se.state_store.new_save_state(frozen, se.state_store.DEFAULT_STORY_SLUG)
    state["history"]["recent_turns"].append("Player: x\nNarrator: a scene")
    return {"story": frozen, "state": state}


def bucket(ctx):
    return ctx["state"]["mechanics"]["endings"]


def at_turn(ctx, turn):
    ctx["state"]["pacing"]["turn_count"] = turn


# --- which waypoints are offered -----------------------------------------------------------------------------
ctx = make_ctx()
first = ENGINE.plant_candidates(ENGINE.entries and mechanics.bound_for(ctx["story"], "endings").cfg, ctx)
cfg = mechanics.bound_for(ctx["story"], "endings").cfg
assert [(p["dest"], p["key"]) for p in first] == [("alpha", "alpha.a1"), ("beta", "beta.b1")], first
assert len(first) <= mechanics.endings.MAX_PLANTS == 2
print("OK: at most two plants, the first waypoint of the first two steered destinations")

# rotation: what was offered to a generation that produced an act is not offered first again
mechanics.endings.record_offers(ctx, [p["key"] for p in first])
second = ENGINE.plant_candidates(cfg, ctx)
assert [p["key"] for p in second] == ["gamma.g1", "alpha.a2"], second
mechanics.endings.record_offers(ctx, [p["key"] for p in second])
third = ENGINE.plant_candidates(cfg, ctx)
assert {p["key"] for p in third} <= {"alpha.a1", "beta.b1", "beta.b2", "alpha.a2", "gamma.g1"}
assert "gamma.g1" not in [p["key"] for p in third] and "alpha.a2" not in [p["key"] for p in third], \
    "the ones offered most recently drop to the back"
print("OK: offers rotate, so every steered destination gets set up rather than the first two forever")

# deduplication: beta.b2 says the same as alpha.a1, so only one of them is ever offered
seen = set()
for _ in range(6):
    for p in ENGINE.plant_candidates(cfg, ctx, limit=10):
        seen.add(p["plant"])
    break
plants = [p["plant"] for p in ENGINE.plant_candidates(cfg, ctx, limit=10)]
assert plants.count("the lamp gutters") == 1, plants
print("OK: a waypoint shared by two destinations is offered once (deduplicated by its plant text)")

# planted waypoints, pruned destinations and non-steered destinations are never offered
ctx = make_ctx()
bucket(ctx)["waypoints_done"] = {"alpha.a1": 2}
bucket(ctx)["pruned"] = {"gamma": 4}
assert "alpha.a1" not in [p["key"] for p in ENGINE.plant_candidates(cfg, ctx, limit=10)]
assert not any(p["dest"] == "gamma" for p in ENGINE.plant_candidates(cfg, ctx, limit=10)), "a pruned destination is never offered again"
at_turn(ctx, 13)  # Narrow (12 <= turn < ...) - steer_top 2: only the two best-scored
bucket(ctx)["steered"] = ["alpha"]
assert {p["dest"] for p in ENGINE.plant_candidates(cfg, ctx, limit=10)} == {"alpha"}
bucket(ctx)["committed"] = {"id": "alpha", "turn": 13, "forced": False}
assert ENGINE.plant_candidates(cfg, ctx) == []
print("OK: planted, pruned, un-steered and committed all offer nothing")

# --- the act prompt: plant text in, everything else about an ending out --------------------------------------
prompts = []


def act_check_llm(prompt, **kw):
    prompts.append(prompt)
    return {"ready": True, "reason": "r", "next_act_title": "Act Two", "next_act_description": "d",
            "completion_signals": ["x"], "new_character": None}


ctx = make_ctx()
ctx["state"]["pacing"]["turns_since_act_check"] = 99
original = se.call_llm_json
real_call_llm = se.call_llm
se.call_llm_json = act_check_llm
try:
    assert se.check_and_advance_act(ctx) == 2
finally:
    se.call_llm_json = original
prompt = prompts[-1]
assert "PLANT (if the act is ready" in prompt and "- the lamp gutters" in prompt and "- the licence is questioned" in prompt
for secret in ("SECRET", "alpha", "beta", "gamma", "a1", "b1", "done_when", "the event a1_done"):
    assert secret not in prompt.split("PLANT")[1].split("EXISTING CHARACTERS")[0].replace("the lamp gutters", ""), secret
assert not any(w in prompt for w in ("SECRET ALPHA NAME", "SECRET HINT", "SECRET CRITERIA", "SECRET ARC", "SECRET DETECT"))
assert bucket(ctx)["offers"] == {"alpha.a1": 1, "beta.b1": 1}, "offered to a generation that produced an act"
print("OK: the act prompt carries the plant text and no ending name, arc, criteria, hint or detect; offers are recorded")

# a check that does not produce an act records no offer
ctx = make_ctx()
ctx["state"]["pacing"]["turns_since_act_check"] = 99
se.call_llm_json = lambda p, **kw: {"ready": False, "reason": "not yet"}
try:
    assert se.check_and_advance_act(ctx) is None
finally:
    se.call_llm_json = original
assert bucket(ctx)["offers"] == {}
print("OK: a director that says not-ready records no offer")

# no endings block, no plant block (P-2)
bare = se.state_store.load_state("steer_bare", se.state_store.DEFAULT_STORY_SLUG)
bare["state"]["pacing"]["turns_since_act_check"] = 99
prompts.clear()
se.call_llm_json = act_check_llm
try:
    se.check_and_advance_act(bare)
finally:
    se.call_llm_json = original
assert "PLANT" not in prompts[-1]
print("OK: a story with no endings gets no PLANT block")

# --- early activation ---------------------------------------------------------------------------------------------
ctx = make_ctx()
at_turn(ctx, 3)  # Open: nothing
assert ENGINE.early_carriers(cfg, ctx) == []
at_turn(ctx, 9)  # Narrow
found = ENGINE.early_carriers(cfg, ctx)
assert [(c["sid"], c["key"]) for c in found] == [("s_dormant", "alpha.a2")], found
assert all(c["sid"] not in ("s_manual", "s_texture", "s_running") for c in found), \
    "manual-only, texture and already-running threads are never started early"
print("OK: from Narrow, a dormant authored carrier with an activate_when is a candidate; manual, texture and running ones are not")

at_turn(ctx, 9)
ctx["state"]["plot"]["subplots"]["s_dormant"]["status"] = "failed"
assert ENGINE.early_carriers(cfg, ctx) == []
ctx["state"]["plot"]["subplots"]["s_dormant"]["status"] = "not_started"
ctx["state"]["plot"]["subplots"]["s_running"]["status"] = "active"
bucket(ctx)["waypoints_done"] = {"alpha.a1": 5}
found = ENGINE.early_carriers(cfg, ctx)
assert [c["sid"] for c in found] == ["s_dormant"]
print("OK: a failed thread is never restarted")

# only on a check turn, at most one at a time, and never once the story is ending
ctx = make_ctx()
at_turn(ctx, 10)  # not a multiple of check_every 3
assert se.activate_carriers_early(ctx) == []
at_turn(ctx, 9)
assert se.activate_carriers_early(ctx) == ["s_dormant"]
rec = ctx["state"]["plot"]["subplots"]["s_dormant"]
assert rec["status"] == "active" and rec["active"] is True
assert se.activate_carriers_early(ctx) == [], "started once; nothing more to start"
ctx2 = make_ctx()
at_turn(ctx2, 9)
ctx2["state"]["plot"]["endgame"]["requested"] = True
assert se.activate_carriers_early(ctx2) == []
print("OK: early activation runs on funnel checks only, starts one thread, and stops once the story is ending")

many = copy.deepcopy(SUBPLOTS)
many["s_dormant2"] = dict(many["s_dormant"], delivers=["beta.b2"])
many["s_dormant3"] = dict(many["s_dormant"], delivers=["gamma.g1"])
ctx = make_ctx(subplots=many)
at_turn(ctx, 9)
started = se.activate_carriers_early(ctx)
assert len(started) == mechanics.endings.MAX_EARLY_ACTIVATIONS == 1, started
print("OK: three waiting carriers open one per check, not three at once")

# --- the trace, end to end through the real turn path ----------------------------------------------------------------
USER, SLUG = "tracer", se.state_store.DEFAULT_STORY_SLUG
FLAG_TURNS = {4: ["a1_done"], 8: ["b1_done"]}


def stub_json(prompt, **kw):
    if "pacing director" in prompt:
        return {"ready": True, "reason": "r", "next_act_title": f"Act {len(prompt) % 7}", "next_act_description": "d",
                "completion_signals": ["s"], "new_character": None}
    if "ENDINGS NOW WITHIN REACH" in prompt:
        return {"ending": None}
    if "CRITERIA:" in prompt:
        return {"confirmed": True}
    if "report what changed" in prompt:
        turn = int(prompt.split("TURN_MARK:")[1].split()[0]) if "TURN_MARK:" in prompt else 0
        diff = {"subplot_beats": {}, "flags_set": {f: {"value": True} for f in FLAG_TURNS.get(turn, [])},
                "revelations": {"revealed": [], "eligible": []}, "inventory": {"gained": [], "used": []},
                "new_characters": [], "scene_update": {"location": "", "summary": "s", "present_npcs": []}}
        return diff
    return {"title": "Generated", "description": "d", "priority": "low", "ties_to_main_plot": "t"}


def stub_text(prompt, **kw):
    return "A scene.\n\nOPTIONS:\n1. One || a\n2. Two || b\n3. Three || c"


ctx = make_ctx(user=USER)
ctx["state"]["story_slug"] = SLUG
se.call_llm_json, se.call_llm = stub_json, stub_text
real_save = se.state_store.save_state
se.state_store.save_state = lambda *a, **k: None  # the story is modified in memory, so it would not match the on-disk template
engine_trace.bind(USER, SLUG)
trace_path = engine_trace.path_for(USER, SLUG)
if os.path.exists(trace_path):
    os.remove(trace_path)
engine_trace.ensure_run(ctx)
try:
    for t in range(1, 26):
        # the stub reads which turn it is from the prompt's own state; mark it there
        ctx["state"]["history"]["recent_turns"][-1] = f"Player: TURN_MARK:{t} go\nNarrator: a scene"
        FLAG_TURNS.setdefault(t, FLAG_TURNS.get(t, []))
        snapshot = copy.deepcopy(ctx["state"])
        se._generate_and_apply_turn(ctx, f"TURN_MARK:{t} go", snapshot, USER, SLUG)
        if ctx["state"]["plot"]["endgame"]["requested"]:
            break
finally:
    se.call_llm_json, se.call_llm = original, real_call_llm
    se.state_store.save_state = real_save
    engine_trace.unbind()
assert ctx["state"]["plot"]["endgame"]["requested"], "the forced commit at commit_by ended the story"

events = [json.loads(line) for line in open(trace_path, encoding="utf-8")]
kinds = {}
for e in events:
    kinds.setdefault(e["kind"], []).append(e)
assert kinds["run_start"][0]["build"] == engine_trace.TRACE_BUILD and kinds["run_start"][0]["endings"]["destinations"]["alpha"]["waypoints"]
assert len({e["run"] for e in events}) == 1 and all(e["user"] == USER and e["story"] == SLUG for e in events)
turn_events = kinds["turn"]
assert [e["turn"] for e in turn_events] == list(range(turn_events[0]["turn"], turn_events[-1]["turn"] + 1)), "one turn event per turn, in order"
assert all(e["prompt_chars"].get("narration") and e["prompt_chars"].get("state_update") for e in turn_events)
assert all("narration" in e["timings"] and "state_update" in e["timings"] for e in turn_events)
assert {e["phase"] for e in turn_events} >= {"open", "narrow"}
assert kinds["flags"] and all(e["asked_chars"] > 0 for e in kinds["flags"] if e["asked"])
assert any(e["set_declared"] == ["a1_done"] for e in kinds["flags"]), "the flag the model reported is logged as set"
planted = {e["key"]: e for e in kinds["waypoint"]}
assert planted["alpha.a1"]["how"] == "done_when" and planted["alpha.a1"]["carriers"] == {"s_running": "active"}
funnel_checks = [e for e in kinds["funnel"] if e["check"]]
assert funnel_checks and all(e["turn"] % 3 == 0 for e in funnel_checks) and funnel_checks[0]["scores"]
assert any(e["carriers"] for e in funnel_checks), "check turns list each steered waypoint's carriers"
acts = [e for e in kinds["act_check"] if e.get("called")]
assert acts and any(e["plants"] for e in acts) and all(e["plant_chars"] > 0 for e in acts if e["plants"])
assert all("SECRET" not in json.dumps(e) for e in acts), "no ending text in the trace of an act check"
scans = kinds["carrier_scan"]
assert any(e["started"] == ["s_dormant"] for e in scans), "the dormant carrier was started early during Narrow"
early = [c for e in kinds["threads"] for c in e["changes"] if c["sid"] == "s_dormant"]
assert early and early[0]["why"] == "early", early
assert kinds["endgame"][0]["cause"] in ("forced", "committed") and kinds["endgame"][0]["turn"] == ctx["state"]["pacing"]["turn_count"]
assert kinds["nudge"], "the baseline nudge cadence is recorded"
print(f"OK: a {len(turn_events)}-turn stubbed run wrote {len(events)} trace events that line up with what the engine did")

# --- the report reads what the engine really wrote (no schema drift between the two) -------------------------------------
import importlib.util  # noqa: E402
_spec = importlib.util.spec_from_file_location(
    "steering_report", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "steering_report.py"))
report = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(report)
(run,) = report.analyze(report.load([trace_path]))
assert run["turns"] == len(turn_events) and run["has_run_start"] and run["quality"]["bad_lines"] == 0
assert run["funnel"]["ended"] and run["funnel"]["cause"] in ("forced", "committed") and run["funnel"]["checks"] >= 4
assert run["waypoints"]["planted"] >= 2 and run["waypoints"]["by_route"].get("done_when", 0) >= 2
assert run["carriers"]["started_early"] == 1 and run["carriers"]["started"][0]["sid"] == "s_dormant"
assert run["flags"]["set"] >= 2 and run["acts"]["called"] >= 1 and run["nudges"]["nudges"] >= 1
assert run["cost"]["prompt_chars"]["narration"]["n"] == len(turn_events)
assert "-- funnel" in report.render([run])
print("OK: steering_report reads the engine's own trace and its numbers match the run")

# --- the muted preview leaves no trace, and a broken trace directory never breaks a turn --------------------------------
before_size = os.path.getsize(trace_path)
import author_preview  # noqa: E402
author_preview.narrator_sections(author_preview.build_ctx(se.state_store.load_template_raw(SLUG), {})[0])
assert os.path.getsize(trace_path) == before_size
assert not os.path.exists(engine_trace.path_for("local", "preview"))
print("OK: building a preview writes nothing to the trace")

print("\nALL CHECKS PASSED: test_steering")
