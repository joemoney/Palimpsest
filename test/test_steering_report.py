"""scripts/steering_report.py: the numbers a reviewer reads. A synthetic trace with known answers, so each metric
is checked against a value worked out by hand, plus the regen and small-sample conventions.

Run directly: python3 test/test_steering_report.py
"""
import importlib.util
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("steering_report", os.path.join(ROOT, "scripts", "steering_report.py"))
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)

RUN, STORY = "r1", "tale"
events = []


def ev(kind, turn, **kw):
    events.append({"v": 1, "ts": "t", "run": RUN, "user": "u", "story": STORY, "turn": turn, "act": 1, "kind": kind, "regen": False, **kw})


ev("run_start", 0, build="steering-1", features={"plant": True}, story_version="v1",
   pacing={"act_check_frequency": 12},
   endings={"budget": {"open_until": 8, "narrow_until": 16, "commit_by": 30}, "check_every": 12, "steer_top": 2,
            "destinations": {"alpha": {"waypoints": [{"id": "a1"}, {"id": "a2"}]}, "beta": {"waypoints": [{"id": "b1"}]}}},
   declared_flags={"f1": True, "f2": True, "f3": False})
for t in range(1, 31):
    phase = "open" if t < 8 else "narrow" if t < 16 else "commit" if t < 30 else "forced"
    ev("turn", t, phase=phase, timings={"narration": {"n": 1, "s": 10.0}, "state_update": {"n": 1, "s": 2.0}},
       prompt_chars={"narration": 4000 + t, "state_update": 3000})
# funnel checks at 12 and 24, with carriers
ev("funnel", 12, phase="narrow", check=True, scores={"alpha": 0.2, "beta": 0.1}, steered=["alpha", "beta"], ready=[], viable=["alpha", "beta"],
   pruned_new={}, planted=[], done=0, viable_waypoints=3, committed=None,
   carriers=[{"key": "alpha.a1", "dest": "alpha", "carriers": {"s1": "active"}},
             {"key": "alpha.a2", "dest": "alpha", "carriers": {"s2": "not_started"}},
             {"key": "beta.b1", "dest": "beta", "carriers": {"s3": "not_started"}}])
ev("funnel", 24, phase="commit", check=True, scores={"alpha": 0.7, "beta": 0.1}, steered=["alpha"], ready=["alpha"], viable=["alpha", "beta"],
   pruned_new={"beta": "viable_while"}, planted=[], done=2, viable_waypoints=2, committed=None, carriers=[])
# acts: offered a1 at 11 (ready), a2 at 23 (not ready)
ev("act_check", 11, called=True, ready=True, due="cadence", plants=[{"key": "alpha.a1", "dest": "alpha", "offers": 0}], plant_chars=40, prompt_chars=3000, new_act=2)
ev("act_check", 20, called=False, skipped="requires_unmet", due="cadence")
ev("act_check", 23, called=True, ready=False, due="completed", plants=[{"key": "alpha.a2", "dest": "alpha", "offers": 0},
                                                                    {"key": "beta.b1", "dest": "beta", "offers": 0}], plant_chars=90, prompt_chars=3100)
# waypoints: a1 planted at 15 (4 after the offer), a2 at 20 after an early carrier, before its offer
ev("waypoint", 15, key="alpha.a1", dest="alpha", how="done_when", carriers={"s1": "active"})
ev("waypoint", 20, key="alpha.a2", dest="alpha", how="detect", carriers={"s2": "active"})
ev("carrier_scan", 12, phase="narrow", candidates=[{"sid": "s2", "key": "alpha.a2", "dest": "alpha"}], started=["s2"])
ev("carrier_scan", 24, phase="commit", candidates=[], started=[])
ev("threads", 12, changes=[{"sid": "s2", "from": "not_started", "to": "active", "why": "early"},
                           {"sid": "s9", "from": "not_started", "to": "active", "why": "condition"}], active=3)
ev("subplot_generated", 14, sid="g1", title="Generated One", span="single_act", priority="low")
# flags: f1 asked 1..5 then set at 5 (and reported false once at 3); f2 asked all 30 turns, never set
for t in range(1, 31):
    asked = (["f1"] if t <= 5 else []) + ["f2"]
    ev("flags", t, asked=asked, asked_chars=100, set_declared=["f1"] if t == 5 else [], dropped_false=["f1"] if t == 3 else [], set_other=1 if t == 9 else 0)
# nudges every 8, a directive fired at 16 (same turn as a nudge), one deferred
for t in (8, 16, 24):
    ev("nudge", t, chars=300, parts=["PACING", "THIS ACT RESOLVES WHEN"], act=1)
ev("pacing_directive", 16, rule="force_wake", fired=True, deferrals=0, reduced=False, counter=5)
ev("pacing_directive", 17, rule="force_wake", fired=False, deferrals=1)
ev("commit_check", 24, ready=["alpha"], chosen=None, judge_null=True, nulls=1, by_null_limit=False)
ev("commit_check", 27, ready=["alpha"], chosen="alpha", judge_null=True, nulls=2, by_null_limit=True)
ev("terminal", 22, id="dead", confirmed=False, has_criteria=True)
ev("endgame", 27, cause="committed", title="Alpha", committed={"id": "alpha"})

runs = report.analyze(events)
assert len(runs) == 1
r = runs[0]
assert r["turns"] == 30 and r["last_turn"] == 30 and r["build"] == "steering-1" and r["has_run_start"]

f = r["funnel"]
assert f["ended"] and f["end_turn"] == 27 and f["cause"] == "committed" and f["budget"]["commit_by"] == 30
assert f["checks"] == 2 and f["steered_set_changes"] == 1
assert f["scores"]["alpha"] == {"first": 0.2, "last": 0.7, "max": 0.7, "n": 2}
assert f["commit_checks"] == 2 and f["judge_nulls"] == 2 and f["committed_by_null_limit"] == 1
assert f["terminal_trips"] == 1 and f["terminal_confirmed"] == 0
assert f["pruned"] == [{"dest": "beta", "turn": 24, "why": "viable_while"}]
assert [(p["phase"], p["from"], p["to"]) for p in map(lambda x: x, f["phase_turns"])][:2] == [("open", 1, 7), ("narrow", 8, 15)]

wp = r["waypoints"]
assert wp["planted"] == 2 and wp["authored"] == 3 and wp["by_route"] == {"done_when": 1, "detect": 1}
assert wp["never_planted"] == ["beta.b1"]
rows = {x["key"]: x for x in wp["rows"]}
assert rows["alpha.a1"]["first_offered"] == 11 and rows["alpha.a1"]["lag_after_offer"] == 4 and not rows["alpha.a1"]["planted_before_offer"]
assert rows["alpha.a2"]["first_offered"] == 23 and rows["alpha.a2"]["planted_before_offer"] and rows["alpha.a2"]["lag_after_offer"] is None
assert wp["planted_before_any_offer"] == 1
assert wp["lag_after_first_offer"] == {"n": 1, "median": 4, "p90": 4, "small_n": True}

st = r["steering"]
# the check at turn 12: a1 was offered at 11 (inside (0,12]) and planted at 15 (inside (12,24]); a2 and b1 were not offered,
# and a2 was planted at 20, b1 never
assert st["offered"] == {"n": 1, "hits": 1, "rate": 1.0, "small_n": True}
assert st["not_offered"] == {"n": 2, "hits": 1, "rate": 0.5, "small_n": True}
assert st["cells"]["offered=True,live_carrier=True"]["hits"] == 1
assert st["cells"]["offered=False,live_carrier=False"] == {"n": 2, "hits": 1, "rate": 0.5, "small_n": True}
assert st["interval_turns"] == 12

a = r["acts"]
assert a["checks"] == 3 and a["skipped_requires_unmet"] == 1 and a["called"] == 2 and a["ready"] == 1
assert a["ready_rate"] == {"n": 2, "hits": 1, "rate": 0.5, "small_n": True} and a["due"] == {"cadence": 1, "completed": 1}
assert a["plants_per_check"] == {"1": 1, "2": 1} and a["waypoints_offered"] == 3
assert a["fairness"] == {"offers_min": 1, "offers_max": 1, "distinct_used": 1}, "only the ready check's plant counts as used"

c = r["carriers"]
assert c["scans"] == 2 and c["scans_with_candidates"] == 1 and c["started_early"] == 1
assert c["started"] == [{"sid": "s2", "turn": 12, "for": "alpha.a2", "planted_turn": 20, "turns_to_plant": 8}]
assert c["steered_waypoint_checks_total"] == 3 and c["steered_waypoint_checks_without_running_carrier"] == 2

t = r["threads"]
assert t["transitions"] == {"not_started->active": 2} and t["activated_by"] == {"early": 1, "condition": 1}
assert t["generated"] == 1 and t["generated_per_100_turns"] == round(100 / 30, 2)

fl = r["flags"]
assert fl["declared_with_detect"] == 2 and fl["set"] == 1 and fl["never_set"] == ["f2"]
assert fl["rows"]["f1"]["asked_turns"] == 5 and fl["rows"]["f1"]["set_turn"] == 5 and fl["rows"]["f1"]["lag"] == 4
assert fl["rows"]["f1"]["dropped_false"] == 1 and fl["rows"]["f2"]["asked_turns"] == 30
assert fl["dropped_false_total"] == 1 and fl["set_other_total"] == 1
assert fl["share_of_state_update_prompt"] == round(30 * 100 / (30 * 3000), 4)

n = r["nudges"]
assert n["nudges"] == 3 and n["turns_between"] == 8 and n["directives_fired"] == 1 and n["directives_deferred"] == 1
assert n["turns_with_nudge_and_directive"] == 1 and n["parts"]["PACING"] == 3

k = r["cost"]
assert k["seconds"]["narration"] == {"calls": 30, "total": 300.0, "p50": 10.0, "p90": 10.0}
assert k["seconds_per_turn"] == 12.0 and k["prompt_chars"]["state_update"]["median"] == 3000
print("OK: every metric matches the hand-worked value on the synthetic run")

# --- regenerated turns: only the re-roll's events survive ---------------------------------------------------------------
regen = [dict(e) for e in events]
first_try = {"v": 1, "ts": "t", "run": RUN, "user": "u", "story": STORY, "turn": 30, "act": 1, "kind": "flags", "regen": False,
             "asked": ["f2"], "asked_chars": 999, "set_declared": ["f2"], "dropped_false": [], "set_other": 0}
reroll = dict(first_try, regen=True, set_declared=[], asked_chars=100)
kept = report.drop_regenerated(regen + [reroll])
turn30 = [e for e in kept if e["turn"] == 30 and e["kind"] == "flags"]
assert turn30 == [reroll], "the original attempt at a re-rolled turn is dropped"
assert len(report.drop_regenerated(regen)) == len(regen)
assert len([e for e in report.drop_regenerated(regen + [first_try, reroll]) if e["turn"] == 29]) == len([e for e in regen if e["turn"] == 29])
print("OK: a re-rolled turn keeps only the re-roll's events; other turns are untouched")

# --- the CLI: files in, text and JSON out, and an empty directory is an error, not a crash ----------------------------------------
with tempfile.TemporaryDirectory() as d:
    path = os.path.join(d, "t.jsonl")
    with open(path, "w") as fh:
        for e in events:
            fh.write(json.dumps(e) + "\n")
        fh.write("not json\n")
    loaded = report.load([path])
    assert len(loaded) == len(events) + 1 and report.analyze(loaded)[0]["quality"]["bad_lines"] == 1
    text = report.render(report.analyze(loaded))
    for heading in ("-- funnel", "-- waypoints", "-- steering association", "-- acts", "-- carriers", "-- declared flags",
                    "-- nudges and directives", "-- cost"):
        assert heading in text, heading
    assert "(small n)" in text and "never set: ['f2']" in text
    assert report.main([path, "--json"]) == 0
    assert report.main([path, "--story", "nope"]) == 0
    os.chdir(d)
    assert report.main([]) == 1, "no traces is reported, not raised"
print("OK: the report renders every section, flags small samples, tolerates a bad line and an empty directory")

print("\nALL CHECKS PASSED: test_steering_report")
