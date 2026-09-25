"""CR-05's ending funnel (`mechanics.endings`, engine `ending_funnel`) - AUTHORING_TOOL_PHASES.md
Phase S5, step 3, first slice: the engine, its state, waypoint planting, pruning, scoring,
the commit judge, the forced commit, terminals, and routing into `_begin_endgame`.

Written against CR-05's own acceptance lines where they apply to this slice:
  - with no destination ready and turn < commit_by, zero commit-judge calls are made;
  - a template without a catch-all destination fails to load;
  - the forced commit at commit_by happens exactly once.

Run directly: python3 test/test_ending_funnel.py
"""
import copy
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _llm_stubs import load_story_engine  # noqa: E402

se = load_story_engine()
mechanics = se.mechanics
ENGINE = mechanics.endings.ENGINE

ENDINGS = {
    "engine": "ending_funnel",
    "check_every": 5,
    "budget": {"open_until": 10, "narrow_until": 20, "commit_by": 30},
    "steer_top": 1,
    "finale_turns": {"min": 2, "max": 4},
    "entries": [
        {"id": "handover", "kind": "destination", "name": "The Handover",
         "viable_while": {"not": {"flag": "lark_departed"}},
         "ready_when": {"all": [{"stat": "quorum", "gte": 65}, {"waypoints_done": "all"}]},
         "criteria": "JUDGE-ONLY SECRET",
         "arc": {"title": "Handover Arc", "description": "Lark takes the seat."},
         "waypoints": [
             {"id": "aboard", "plant": "Lark comes aboard", "done_when": {"flag": "lark_aboard"}},
             {"id": "reacts", "plant": "The system reacts to Lark",
              "detect": "the ship withholds in Lark's presence"}]},
        {"id": "still_flying", "kind": "destination", "name": "Still Flying",
         "waypoints": [{"id": "licence", "plant": "The licence is renewed or forged",
                        "detect": "a licence changes hands"}]},
        {"id": "dead_weight", "kind": "terminal", "min_turn": 3,
         "ready_when": {"stat": "frame", "lte": 0},
         "criteria": "the scene was lethal", "arc": {"title": "Dead Weight", "description": "It ends."}},
    ],
}


def make_ctx(endings=ENDINGS, subplots=None):
    """A fresh save of the default story with `endings` (and optionally `subplots`) authored
    and a flags block declaring what these tests set. Built through new_save_state so the
    engine's init_state is exercised the way a real save creation exercises it."""
    ctx = se.state_store.load_state("funneltest", se.state_store.DEFAULT_STORY_SLUG)
    story = se.state_store.thaw(ctx["story"])
    story.setdefault("mechanics", {})["endings"] = copy.deepcopy(endings)
    story["mechanics"]["flags"] = {"declared": [{"id": f, "detect": f} for f in
                                                ("lark_departed", "lark_aboard")]}
    if subplots is not None:
        story["plot"]["subplots"] = subplots
    frozen = se.state_store.freeze(story)
    mechanics.validate(frozen)
    state = se.state_store.new_save_state(frozen, se.state_store.DEFAULT_STORY_SLUG)
    state["protagonist"].setdefault("stats", {}).update({"quorum": 0, "frame": 5})
    state["history"]["recent_turns"].append("Player: x\nNarrator: a scene")
    return {"story": frozen, "state": state}


def bucket(ctx):
    return ctx["state"]["mechanics"]["endings"]


def at_turn(ctx, turn):
    ctx["state"]["pacing"]["turn_count"] = turn


class Judge:
    """Scripted call_llm_json: answers by which judge is asking, and records every call."""
    def __init__(self, commit=None, confirm=True):
        self.commit, self.confirm, self.calls = list(commit or []), confirm, []

    def __call__(self, prompt, **kw):
        if "ENDINGS NOW WITHIN REACH" in prompt:
            self.calls.append(("commit", prompt))
            return {"ending": self.commit.pop(0) if self.commit else None}
        if "CRITERIA:" in prompt:
            self.calls.append(("confirm", prompt))
            return {"confirmed": self.confirm}
        raise AssertionError(f"unexpected LLM call: {prompt[:80]}")


# --- load-time: registered, and a catch-all is required --------------------------------
ctx = make_ctx()
assert bucket(ctx) == {"pruned": {}, "waypoints_done": {}, "scores": {}, "steered": [],
                       "judge_nulls": 0, "terminal_cooldown": {}, "committed": None}
no_catch_all = copy.deepcopy(ENDINGS)
no_catch_all["entries"][1]["viable_while"] = {"turn_gte": 0}
try:
    make_ctx(no_catch_all)
    raise AssertionError("a template with no catch-all destination must fail to load")
except ValueError as exc:
    assert "catch-all" in str(exc), exc
try:
    make_ctx({"engine": "ending_funnel"})
    raise AssertionError("an ending_funnel with no entries must not load inert")
except ValueError as exc:
    assert "entries" in str(exc), exc
print("OK: ending_funnel is registered, seeds its state, and refuses a story with no catch-all")

# --- waypoints: done_when in code (CLOSED), detect through the one observation field -----
ctx = make_ctx()
fields = [f for f in mechanics.observation_fields(ctx) if f.name == "waypoints_hit"]
assert len(fields) == 1, "the funnel asks exactly one field"
field = fields[0]
assert "the ship withholds" in field.context and "a licence changes hands" in field.context
for leak in ("handover", "still_flying", "The Handover", "JUDGE-ONLY", "Lark comes aboard"):
    assert leak not in field.context, f"{leak!r} leaked into the observation pass"
events = ENGINE.events(ENDINGS, ctx, {"waypoints_hit": [1, 99, "x"]})
assert events == [{"type": "waypoint_hit", "key": "handover.reacts"}], events
at_turn(ctx, 2)
mechanics.apply_effects(ctx, ENGINE.resolve(ENDINGS, ctx, events, []))
assert bucket(ctx)["waypoints_done"] == {"handover.reacts": 2}

ctx["state"]["protagonist"]["flags"]["active"]["lark_aboard"] = True
at_turn(ctx, 3)
mechanics.apply_effects(ctx, ENGINE.settle(ENDINGS, ctx))
assert bucket(ctx)["waypoints_done"] == {"handover.reacts": 2, "handover.aboard": 3}
# Nothing left to detect for handover: the field narrows to the other destination.
field = [f for f in mechanics.observation_fields(ctx) if f.name == "waypoints_hit"][0]
assert "the ship withholds" not in field.context and "a licence changes hands" in field.context
print("OK: done_when plants in code, detect plants through waypoints_hit, and nothing leaks")

# --- conditions read the ledger: ready_when's waypoints_done is scoped to its own ending --
ctx["state"]["protagonist"]["stats"]["quorum"] = 70
assert [e["id"] for e in ENGINE.ready(ENDINGS, ctx)] == ["handover"]
print("OK: ready_when reads the funnel's own ledger")

# --- no judge call while nothing is ready, or off a check -------------------------------
ctx = make_ctx()
judge = Judge()
se.call_llm_json = judge
for turn in range(1, 30):
    at_turn(ctx, turn)
    assert se.check_ending_funnel(ctx) is None
assert judge.calls == [], judge.calls
print("OK: zero judge calls with no destination ready and turn < commit_by")

# --- pruning: viable_while (OPEN), permanent, and a catch-all is never pruned ------------
ctx = make_ctx()
ctx["state"]["protagonist"]["flags"]["active"]["lark_departed"] = True
at_turn(ctx, 4)
se.check_ending_funnel(ctx)
assert bucket(ctx)["pruned"] == {}, "pruning happens at checks, not every turn"
at_turn(ctx, 5)
se.check_ending_funnel(ctx)
assert bucket(ctx)["pruned"] == {"handover": 5}
del ctx["state"]["protagonist"]["flags"]["active"]["lark_departed"]
at_turn(ctx, 10)
se.check_ending_funnel(ctx)
assert bucket(ctx)["pruned"] == {"handover": 5}, "pruning is permanent"
assert "handover" not in bucket(ctx)["scores"]
field = [f for f in mechanics.observation_fields(ctx) if f.name == "waypoints_hit"][0]
assert "the ship withholds" not in field.context, "a pruned ending's waypoints are never asked"
print("OK: viable_while prunes at a check, permanently, and pruned waypoints drop out")

# --- pruning: every remaining carrier failed (CR-10) -----------------------------------
subplots = {"sp_diver": {"title": "The Diver", "description": "d", "starts_active": True,
                         "delivers": ["handover.aboard", "handover.reacts"]},
            "sp_other": {"title": "Other", "description": "o", "starts_active": True,
                         "delivers": ["still_flying.licence"]}}
ctx = make_ctx(subplots=subplots)
ctx["state"]["plot"]["subplots"]["sp_diver"].update(status="failed", active=False)
ctx["state"]["plot"]["subplots"]["sp_other"].update(status="failed", active=False)
at_turn(ctx, 5)
se.check_ending_funnel(ctx)
assert bucket(ctx)["pruned"] == {"handover": 5}, bucket(ctx)["pruned"]
print("OK: a destination whose remaining waypoints are carried only by failed threads is "
      "pruned; the catch-all never is")

# --- narrowing: steer_top by score from open_until -------------------------------------
ctx = make_ctx()
at_turn(ctx, 5)
se.check_ending_funnel(ctx)
assert bucket(ctx)["steered"] == ["handover", "still_flying"], "Open steers every viable ending"
ctx["state"]["protagonist"]["flags"]["active"]["lark_aboard"] = True
at_turn(ctx, 10)
se.check_ending_funnel(ctx)
assert bucket(ctx)["steered"] == ["handover"], bucket(ctx)
assert bucket(ctx)["scores"]["handover"] > bucket(ctx)["scores"]["still_flying"]
print("OK: from open_until only the top steer_top destinations are steered")

# --- commit judge: chooses among the ready set; null twice commits the leader -----------
ctx = make_ctx()
ctx["state"]["protagonist"]["flags"]["active"]["lark_aboard"] = True
bucket(ctx)["waypoints_done"]["handover.reacts"] = 1
ctx["state"]["protagonist"]["stats"]["quorum"] = 70
judge = Judge(commit=[None, None])
se.call_llm_json = judge
at_turn(ctx, 5)
assert se.check_ending_funnel(ctx) is None and judge.calls == [], "not before open_until"
at_turn(ctx, 10)
assert se.check_ending_funnel(ctx) is None
assert bucket(ctx)["judge_nulls"] == 1
assert "JUDGE-ONLY SECRET" in judge.calls[0][1], "the commit judge sees criteria"
at_turn(ctx, 15)
committed = se.check_ending_funnel(ctx)
assert committed["id"] == "handover" and len(judge.calls) == 2
assert bucket(ctx)["committed"] == {"id": "handover", "turn": 15, "forced": False}
endgame = ctx["state"]["plot"]["endgame"]
assert endgame["requested"] and endgame["cause"] == "committed"
assert endgame["final_arc"] == {"title": "Handover Arc", "description": "Lark takes the seat."}
assert se._all_acts(ctx)[-1]["is_finale"] is True
# Once committed, the funnel stops: no more calls, no further commit, no observation field.
at_turn(ctx, 20)
assert se.check_ending_funnel(ctx) is None and len(judge.calls) == 2
assert not [f for f in mechanics.observation_fields(ctx) if f.name == "waypoints_hit"]
print("OK: the commit judge picks among ready endings; two nulls commit the leader")

ctx = make_ctx()
ctx["state"]["protagonist"]["flags"]["active"]["lark_aboard"] = True
bucket(ctx)["waypoints_done"]["handover.reacts"] = 1
ctx["state"]["protagonist"]["stats"]["quorum"] = 70
se.call_llm_json = Judge(commit=[1])
at_turn(ctx, 10)
assert se.check_ending_funnel(ctx)["id"] == "handover"
print("OK: a judge answer commits on the spot")

# --- forced commit at commit_by, exactly once, with a bridging note ---------------------
ctx = make_ctx()
judge = Judge()
se.call_llm_json = judge
at_turn(ctx, 30)
forced = se.check_ending_funnel(ctx)
assert forced["id"] == "handover", forced  # tied scores: authored order wins
assert bucket(ctx)["committed"] == {"id": "handover", "turn": 30, "forced": True}
arc = ctx["state"]["plot"]["endgame"]["final_arc"]
assert "Lark comes aboard" in arc["description"] and "The system reacts to Lark" in arc["description"]
assert ctx["state"]["plot"]["endgame"]["cause"] == "forced"
finales = [a for a in se._all_acts(ctx) if a.get("is_finale")]
at_turn(ctx, 31)
assert se.check_ending_funnel(ctx) is None
assert len([a for a in se._all_acts(ctx) if a.get("is_finale")]) == len(finales) == 1
assert judge.calls == [], "a forced commit asks no judge"
print("OK: the forced commit at commit_by happens exactly once, bridging unplanted waypoints")

# --- an ending with no authored arc falls back to its name, never its criteria ----------
bare = ENGINE.final_arc({"id": "x", "name": "Plain", "criteria": "SECRET", "_theme": "PRIVATE"})
assert bare["title"] == "Plain" and "SECRET" not in bare["description"] and "PRIVATE" not in bare["description"]
print("OK: an ending with no arc falls back to its name alone")

# --- terminals: every turn after min_turn, judge-confirmed, cooldown on a no ------------
ctx = make_ctx()
ctx["state"]["protagonist"]["stats"]["frame"] = 0
judge = Judge(confirm=False)
se.call_llm_json = judge
at_turn(ctx, 2)
assert se.check_ending_funnel(ctx) is None and judge.calls == [], "not before min_turn"
at_turn(ctx, 3)
assert se.check_ending_funnel(ctx) is None and len(judge.calls) == 1
assert bucket(ctx)["terminal_cooldown"] == {"dead_weight": 8}
at_turn(ctx, 4)
se.check_ending_funnel(ctx)
assert len(judge.calls) == 1, "a declined terminal waits out its cooldown"
judge.confirm = True
at_turn(ctx, 8)
assert se.check_ending_funnel(ctx)["id"] == "dead_weight"
assert ctx["state"]["plot"]["endgame"]["cause"] == "terminal"
assert ctx["state"]["plot"]["endgame"]["final_arc"]["title"] == "Dead Weight"
print("OK: a terminal trips any turn past min_turn, is judge-confirmed, and cools down on a no")

# --- the finale: cause-aware opener, and finale_turns bounds -----------------------------
ctx = make_ctx()
at_turn(ctx, 30)
se.call_llm_json = Judge()
se.check_ending_funnel(ctx)
section = se._section_pacing_or_endgame(ctx)
assert "The story has reached its ending." in section and "The player has asked" not in section
assert "Do not conclude in this scene" in section, "finale_turns.min holds the first scene back"
at_turn(ctx, 32)
assert "Do not conclude" not in se._section_pacing_or_endgame(ctx)
at_turn(ctx, 33)
assert 'This must be the final scene' in se._section_pacing_or_endgame(ctx)
print("OK: the finale prompt names no player request, and finale_turns bounds its length")

# --- once committed, subplot generation and act advancement no-op ------------------------
assert se.generate_new_subplot(ctx) is None
print("OK: a committed ending stops subplot generation, as endgame.requested always has")

print("\nALL CHECKS PASSED: test_ending_funnel")
