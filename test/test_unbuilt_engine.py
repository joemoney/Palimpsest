"""The last of Story_Mechanics_Update's engine pieces: CR-01 tier `on_enter`, CR-07 thread
completion rewards, CR-08 relationship transitions, `max_acts`, and the concluded epilogue.
Run directly: python3 test/test_unbuilt_engine.py"""
import copy
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _llm_stubs import load_story_engine  # noqa: E402

se = load_story_engine()
mechanics = se.mechanics

STATS = {"engine": "bounded_counter", "axes": {
    "noise": {"costs": {"loud": 10, "reclaim": -5}, "tiers": [
        {"at": 0, "label": "quiet"},
        {"at": 25, "label": "heard", "on_enter": {"once": True, "directive": "The belt goes quiet."}},
        {"at": 50, "label": "hunted", "on_enter": {"directive": "Something answers."}}]}}}


def ctx_for(story_mech, stats=None, turn=3, extra_state=None):
    ctx = {"story": {"world": {"characters": {}}, "plot": {"subplots": {}, "main_thread": {"acts": []}},
                     "mechanics": story_mech},
           "state": {"protagonist": {"stats": dict(stats or {}), "flags": {"active": {}, "archive": {}, "meta": {}}},
                     "characters": {}, "pacing": {"turn_count": turn}, "mechanics": {},
                     "plot": {"subplots": {}}}}
    ctx["state"].update(extra_state or {})
    return ctx


def run_stat_turn(ctx, keys, turn):
    ctx["state"]["pacing"]["turn_count"] = turn
    b = mechanics.bound_for(ctx["story"], "stats")
    mechanics.apply_effects(ctx, b.engine.resolve(b.cfg, ctx, [{"type": "stat_event", "key": k} for k in keys], []))


# --- CR-01 -----------------------------------------------------------------------------
ctx = ctx_for({"stats": STATS}, {"noise": 20})
run_stat_turn(ctx, ["loud"], 4)  # 20 -> 30 crosses "heard"
assert ctx["state"]["mechanics"]["stats"]["tier_log"] == {"noise": ["heard"]}
assert "The belt goes quiet." in mechanics.prompt_sections(ctx)["stats.directive"]
ctx["state"]["pacing"]["turn_count"] = 5
assert "stats.directive" not in mechanics.prompt_sections(ctx), "the directive lasts one scene"
run_stat_turn(ctx, ["reclaim"], 6)  # 25: still in heard
run_stat_turn(ctx, ["reclaim"], 7)  # 20: below
run_stat_turn(ctx, ["loud"], 8)     # 30: re-enters; once -> logged but no directive
assert "stats.directive" not in mechanics.prompt_sections(ctx), "once: no refire"
run_stat_turn(ctx, ["loud", "loud", "loud"], 9)  # 30 -> 60 crosses hunted
assert "Something answers." in mechanics.prompt_sections(ctx)["stats.directive"]
assert ctx["state"]["mechanics"]["stats"]["tier_log"]["noise"] == ["heard", "hunted"]
jump = ctx_for({"stats": STATS}, {"noise": 0})
run_stat_turn(jump, ["loud"] * 6, 4)  # 0 -> 60: one directive, the highest authored
text = mechanics.prompt_sections(jump)["stats.directive"]
assert "Something answers." in text and "belt goes quiet" not in text
assert jump["state"]["mechanics"]["stats"]["tier_log"]["noise"] == ["heard", "hunted"]
dip = ctx_for({"stats": STATS}, {"noise": 20})
run_stat_turn(dip, ["loud", "reclaim"], 4)  # net 20 -> 25: crosses heard
assert dip["state"]["mechanics"]["stats"]["tier_log"] == {"noise": ["heard"]}
print("OK: CR-01 on_enter fires on upward crossings only, once per scene, honours once")

# --- CR-07 -----------------------------------------------------------------------------
SUBS = {"engine": "weighted_threads", "completion_rewards": {"high": ["reclaim"], "low": []},
        "near_completion_margin": 15}
mech = {"stats": STATS, "subplots": SUBS}


def thread_ctx():
    ctx = ctx_for(mech, {"noise": 30})
    ctx["story"]["plot"]["subplots"] = {
        "a": {"title": "A", "priority": "high", "completion_threshold": 100},
        "b": {"title": "B", "priority": "low", "completion_threshold": 100,
              "on_complete": {"stat_events": ["reclaim", "reclaim"]}}}
    ctx["state"]["plot"]["subplots"] = {
        "a": {"status": "active", "active": True, "progress": 90},
        "b": {"status": "active", "active": True, "progress": 0}}
    return ctx


ctx = thread_ctx()
assert "'A' may resolve this scene" in mechanics.prompt_sections(ctx)["subplots.arm"]
mechanics.apply_effects(ctx, [mechanics.Effect("subplots.progress", id="a", value=100, unshown=False)])
mechanics.settle_all(ctx)
assert ctx["state"]["protagonist"]["stats"]["noise"] == 25
mechanics.settle_all(ctx)
mechanics.settle_all(ctx)
assert ctx["state"]["protagonist"]["stats"]["noise"] == 25, "paid exactly once"
assert ctx["state"]["plot"]["subplots"]["a"]["reward_paid"]
snapshot = copy.deepcopy(thread_ctx()["state"])
redo = thread_ctx()
redo["state"] = snapshot
mechanics.apply_effects(redo, [mechanics.Effect("subplots.progress", id="a", value=100, unshown=False)])
mechanics.settle_all(redo)
assert redo["state"]["protagonist"]["stats"]["noise"] == 25, "a restored snapshot pays once again, not twice"

ctx = thread_ctx()  # own on_complete overrides the priority row; low priority has no row
mechanics.apply_effects(ctx, [mechanics.Effect("subplots.progress", id="b", value=100, unshown=True)])
mechanics.settle_all(ctx)
assert ctx["state"]["protagonist"]["stats"]["noise"] == 20
ctx["state"]["pacing"]["turn_count"] = 3
assert "PAYOFF IS OWED" in mechanics.prompt_sections(ctx)["subplots.directive"]
ctx["state"]["pacing"]["turn_count"] = 4
assert "subplots.directive" not in mechanics.prompt_sections(ctx)
field = mechanics.bound_for(ctx["story"], "subplots").engine.observations(
    mechanics.bound_for(ctx["story"], "subplots").cfg, ctx)
assert "resolved_unshown" in field[0].schema
plain = ctx_for({"subplots": {"engine": "weighted_threads"}})
plain["state"]["plot"]["subplots"] = {"a": {"status": "active", "active": True, "progress": 0}}
plain["story"]["plot"]["subplots"] = {"a": {"title": "A", "priority": "high"}}
assert "resolved_unshown" not in mechanics.bound_for(plain["story"], "subplots").engine.observations(
    {"engine": "weighted_threads"}, plain)[0].schema
print("OK: CR-07 pays once, overrides by on_complete, owes an unshown payoff for one scene")

# --- CR-08 -----------------------------------------------------------------------------
REL = {"engine": "scored_axis", "registers": {"warm": 10}, "transitions": [
    {"id": "drifting", "when": {"peak_gte": 25, "between": [-10, 10]},
     "directive": "{name} is leaving: give them an exit that costs something.", "sets_flag": "{id}_departed"}]}


def rel_ctx(score, peak=None):
    ctx = ctx_for({"relationships": REL})
    ctx["state"]["characters"] = {"Lark Ferris": {"relationship": score, **({"peak": peak} if peak is not None else {})}}
    return ctx


def settle_at(ctx, turn):
    ctx["state"]["pacing"]["turn_count"] = turn
    mechanics.settle_all(ctx)


ctx = rel_ctx(5, peak=40)
settle_at(ctx, 10)
assert ctx["state"]["characters"]["Lark Ferris"]["exiting"]
assert "Lark Ferris is leaving" in mechanics.prompt_sections(ctx)["relationships.directive"]
settle_at(ctx, 10)
assert ctx["state"]["mechanics"]["relationships"]["transitions"]["Lark Ferris"]["drifting"]["turn"] == 10
assert not ctx["state"]["characters"]["Lark Ferris"].get("departed"), "not until the exit scene has passed"
assert "lark_departed" not in ctx["state"]["protagonist"]["flags"]["active"]
settle_at(ctx, 11)
rec = ctx["state"]["characters"]["Lark Ferris"]
assert rec["departed"] and "exiting" not in rec
assert ctx["state"]["protagonist"]["flags"]["active"]["lark_departed"] is True
assert "Lark Ferris" not in mechanics.prompt_sections(ctx).get("relationships.player_line", "")
settle_at(ctx, 12)
assert "relationships.directive" not in mechanics.prompt_sections(ctx)
never = rel_ctx(5, peak=20)
settle_at(never, 10)
assert "exiting" not in never["state"]["characters"]["Lark Ferris"], "peak below 25 never fires"
unmet = rel_ctx(5)
settle_at(unmet, 10)
assert "exiting" not in unmet["state"]["characters"]["Lark Ferris"], "no peak: the score is the peak"

b = mechanics.bound_for(ctx["story"], "relationships")
fresh = rel_ctx(0)
fresh["state"]["characters"]["Lark Ferris"]["relationship"] = 30
mechanics.apply_effects(fresh, [mechanics.Effect("relationships.set", target="Lark Ferris", value=30, delta=30,
                                                 windowed=False, turn=1)])
assert fresh["state"]["characters"]["Lark Ferris"]["peak"] == 30
print("OK: CR-08 fires once, departs the turn after, sets the flag, and respects peak")

# --- max_acts ----------------------------------------------------------------------------
def acts_ctx(max_acts, authored=1):
    ctx = se.state_store.load_state("unbuilt_acts", se.state_store.DEFAULT_STORY_SLUG)
    story = se.state_store.thaw(ctx["story"])
    story["plot"]["main_thread"]["acts"] = [
        {"act_number": n, "title": f"T{n}", "description": f"d{n}"} for n in range(1, authored + 1)]
    story["plot"]["main_thread"]["max_acts"] = max_acts
    ctx["story"] = se.state_store.freeze(story)
    ctx["state"]["plot"]["act_completion"] = {str(n): {"completed": False, "optional": False}
                                               for n in range(1, authored + 1)}
    ctx["state"]["plot"]["current_act"] = authored
    ctx["state"]["plot"]["generated_acts"] = []
    ctx["state"]["pacing"]["turns_since_act_check"] = 99
    return ctx


calls = []
se.call_llm_json = lambda p, **kw: calls.append(p) or {
    "ready": True, "reason": "r", "next_act_title": "G", "next_act_description": "d",
    "completion_signals": ["s"], "new_character": None}
capped = acts_ctx(max_acts=1)
assert se.check_and_advance_act(capped) is None and not calls, "at the cap: no Tier B call, no new act"
assert capped["state"]["plot"]["current_act"] == 1
roomy = acts_ctx(max_acts=2)
assert se.check_and_advance_act(roomy) == 2 and calls, "under the cap an act is generated"
ahead = acts_ctx(max_acts=2, authored=3)
ahead["state"]["plot"]["current_act"] = 1
calls.clear()
assert se.check_and_advance_act(ahead) == 2, "an authored act ahead is never blocked by the cap"
print("OK: max_acts stops generation before the Tier B call and never blocks authored acts")

# --- epilogue ----------------------------------------------------------------------------
ctx = se.state_store.load_state("unbuilt_epilogue", se.state_store.DEFAULT_STORY_SLUG)
story = se.state_store.thaw(ctx["story"])
story.setdefault("mechanics", {})["endings"] = {"engine": "ending_funnel", "entries": [
    {"id": "calm", "kind": "destination", "name": "Calm", "arc": {"title": "t", "description": "d"},
     "waypoints": [], "epilogue": "  Years later, the belt hums.  "},
    {"id": "bare", "kind": "destination", "name": "Bare", "arc": {"title": "t", "description": "d"}, "waypoints": []}]}
ctx["story"] = se.state_store.freeze(story)
assert se.concluded_epilogue(ctx) == "", "not concluded"
ctx["state"]["plot"]["endgame"]["concluded"] = True
ctx["state"].setdefault("mechanics", {}).setdefault("endings", {})["committed"] = {"id": "calm"}
assert se.concluded_epilogue(ctx) == "Years later, the belt hums."
ctx["state"]["mechanics"]["endings"]["committed"] = {"id": "bare"}
assert se.concluded_epilogue(ctx) == ""
print("OK: the concluded epilogue is the committed ending's, and nothing before then")
