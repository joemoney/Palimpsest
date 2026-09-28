#!/usr/bin/env python3
"""Quantify the steering engine from its trace (backend/engine_trace.py).

    python3 scripts/steering_report.py                       # every trace under data/traces
    python3 scripts/steering_report.py data/traces/u/the_missing_core.jsonl
    python3 scripts/steering_report.py --story the_missing_core --run ab12cd34ef56
    python3 scripts/steering_report.py --json                # machine-readable, to diff two builds

The questions it answers, each with the count behind it (a small n is flagged, not hidden):

  funnel      when the story ended and by what route; how the destinations' scores moved; how stable the
              steered set was; how often the commit judge said "not now"; what was pruned and why.
  waypoints   when each was planted and by which route (`done_when` or `detect`); how long after it was first
              offered to an act generation; and the steering estimate below.
  steering    of the (waypoint, funnel check) pairs where a steered waypoint was still unplanted, the share
              planted within one act-check interval, split by whether it had been offered (to an act
              generation, or named in a pacing nudge) in the interval before, and by surface. NOT a causal estimate - a waypoint is offered because it
              is unplanted, and an unplanted waypoint with a live carrier plants sooner anyway - but the
              gap and its sample size are the first thing to look at, and `carrier state` splits it.
  acts        how often the act director is called, how often it says ready, how many plants it was shown,
              and whether the offers rotate (fairness).
  carriers    how often a steered waypoint had no running carrier, what early activation started, and
              what became of the waypoints those threads carried.
  flags       for each declared flag: turns asked about, the turn it was set, the lag; false reports dropped;
              the prompt cost of asking.
  nudges      how often the pacing nudge fires and what is in it; directives fired and deferred; turns where
              both landed.
  story clock how many turns were idle, how many of those were free and how many paid, the longest idle streak,
              which signals made the other turns count, how often the push fired (or yielded to a pacing rule),
              how many turns the options leaned forward, and how far the story clock ended behind the turn count.
  nudge steering  what steering added to those nudges: carrier lines carrying a plant, how often the boost
              changed which thread led, hints (and whether they rotate), drive nudges, how often a drive
              nudge yielded to an armed pacing rule, and the characters it all added.
  cost        seconds and prompt characters per step, the share steering text takes of the prompts, and how the
              real narration prompt sizes compare to the author's own token budget (NARRATION_TOKEN_BUDGET).

Regenerated turns: events written while re-rolling the last turn are kept and the first attempt's events
for that turn dropped (`--keep-regen` keeps both).

Offline, stdlib only, no network.
"""
import argparse
import collections
import glob
import json
import os
import statistics
import sys

SMALL_N = 8
# Mirrors backend/author_lint.py's NARRATION_TOKEN_BUDGET, which this script cannot import
# without pulling in jsonschema/dotenv (its own docstring: offline, stdlib only). Two copies,
# not one shared constant, is the same tension CLAUDE.md already names for STATUS_LABELS /
# DEFAULT_STEP_ESTIMATE_SECONDS; test_steering_report.py asserts the two stay equal.
NARRATION_TOKEN_BUDGET = 20000


def load(paths):
    events = []
    for path in paths:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    events.append({"kind": "_bad_line", "turn": None, "run": None})
    return events


def drop_regenerated(events):
    """For a turn that was re-rolled, keep only the re-roll's events."""
    rerolled = {(e.get("run"), e.get("turn")) for e in events if e.get("regen")}
    return [e for e in events if e.get("regen") or (e.get("run"), e.get("turn")) not in rerolled]


def pct(values, p):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round((p / 100.0) * (len(ordered) - 1))))]


def med(values):
    return round(statistics.median(values), 2) if values else None


def rate(hits, n):
    return {"n": n, "hits": hits, "rate": round(hits / n, 3) if n else None, "small_n": n < SMALL_N}


def offer_events(kinds):
    """`[(turn, key, surface)]` for every time a waypoint's plant was put in front of a model: an act check
    that was called (surface `act`), a carrier's line in a pacing nudge (`nudge`) or a drive nudge (`drive`)."""
    out = []
    for e in kinds.get("act_check", []):
        if e.get("called"):
            out += [(e["turn"], p["key"], "act") for p in e.get("plants") or []]
    for e in kinds.get("nudge", []):
        out += [(e["turn"], p["key"], "nudge") for p in e.get("carrier_plants") or []]
        out += [(e["turn"], k, "drive") for k in (e.get("drive") or {}).get("keys") or []]
    return out


def by_kind(events):
    out = collections.defaultdict(list)
    for e in events:
        out[e.get("kind")].append(e)
    return out


# --- one run ---------------------------------------------------------------------------------------------

def analyze_run(events):
    kinds = by_kind(events)
    start = (kinds.get("run_start") or [{}])[0]
    turns = sorted({e["turn"] for e in kinds.get("turn", []) if e.get("turn") is not None})
    last_turn = turns[-1] if turns else None
    out = {"run": (events[0].get("run") if events else None), "story": (events[0].get("story") if events else None),
           "build": start.get("build"), "features": start.get("features"), "story_version": start.get("story_version"),
           "turns": len(turns), "last_turn": last_turn, "events": len(events), "has_run_start": bool(start)}
    out["quality"] = {"regen_events": sum(1 for e in events if e.get("regen")),
                      "turns_without_turn_event": (last_turn - len(turns)) if last_turn else 0,
                      "bad_lines": 0}
    out["funnel"] = _funnel(kinds, start)
    out["waypoints"] = _waypoints(kinds, start)
    out["steering"] = _steering(kinds, start)
    out["acts"] = _acts(kinds)
    out["carriers"] = _carriers(kinds)
    out["threads"] = _threads(kinds, last_turn)
    out["flags"] = _flags(kinds, start)
    out["nudges"] = _nudges(kinds)
    out["nudge_steering"] = _nudge_steering(kinds)
    out["clock"] = _clock(kinds, start)
    out["cost"] = _cost(kinds)
    return out


def _funnel(kinds, start):
    cfg = start.get("endings") or {}
    endgame = (kinds.get("endgame") or [None])[0]
    checks = [e for e in kinds.get("funnel", []) if e.get("check")]
    scores = collections.defaultdict(list)
    for e in checks:
        for dest, s in (e.get("scores") or {}).items():
            scores[dest].append(s)
    steered_changes = sum(1 for a, b in zip(checks, checks[1:]) if (a.get("steered") or []) != (b.get("steered") or []))
    ccs = kinds.get("commit_check", [])
    terminals = kinds.get("terminal", [])
    phases = []
    for e in kinds.get("turn", []):
        if not phases or phases[-1][0] != e.get("phase"):
            phases.append([e.get("phase"), e["turn"], e["turn"]])
        else:
            phases[-1][2] = e["turn"]
    return {
        "budget": cfg.get("budget"), "check_every": cfg.get("check_every"), "steer_top": cfg.get("steer_top"),
        "ended": bool(endgame), "end_turn": endgame["turn"] if endgame else None,
        "cause": endgame.get("cause") if endgame else None, "title": endgame.get("title") if endgame else None,
        "phase_turns": [{"phase": p, "from": a, "to": b} for p, a, b in phases],
        "checks": len(checks),
        "scores": {d: {"first": v[0], "last": v[-1], "max": max(v), "n": len(v)} for d, v in scores.items()},
        "steered_set_changes": steered_changes,
        "commit_checks": len(ccs), "judge_nulls": sum(1 for e in ccs if e.get("judge_null")),
        "committed_by_null_limit": sum(1 for e in ccs if e.get("by_null_limit")),
        "terminal_trips": len(terminals), "terminal_confirmed": sum(1 for e in terminals if e.get("confirmed")),
        "pruned": [{"dest": d, "turn": e["turn"], "why": why} for e in kinds.get("funnel", [])
                   for d, why in (e.get("pruned_new") or {}).items()],
    }


def _waypoints(kinds, start):
    offers = collections.defaultdict(list)
    for turn, key, surface in offer_events(kinds):
        offers[key].append((turn, surface))
    rows = []
    for e in kinds.get("waypoint", []):
        first_offer = min(offers.get(e["key"], []), default=(None, None))
        first = first_offer[0]
        rows.append({"key": e["key"], "turn": e["turn"], "how": e.get("how"), "first_offered": first,
                     "first_offer_surface": first_offer[1],
                     "lag_after_offer": (e["turn"] - first) if first is not None and e["turn"] >= first else None,
                     "planted_before_offer": first is None or e["turn"] < first, "carriers": e.get("carriers")})
    planted = {r["key"] for r in rows}
    declared = {f"{d}.{w['id']}": w for d, info in ((start.get("endings") or {}).get("destinations") or {}).items()
                for w in info.get("waypoints") or []}
    lags = [r["lag_after_offer"] for r in rows if r["lag_after_offer"] is not None]
    return {
        "planted": len(rows), "authored": len(declared) or None,
        "by_route": dict(collections.Counter(r["how"] for r in rows)),
        "plant_turn_median": med([r["turn"] for r in rows]),
        "never_planted": sorted(k for k in declared if k not in planted),
        "planted_before_any_offer": sum(1 for r in rows if r["planted_before_offer"]),
        "lag_after_first_offer": {"n": len(lags), "median": med(lags), "p90": pct(lags, 90), "small_n": len(lags) < SMALL_N},
        "rows": rows,
    }


def _steering(kinds, start):
    interval = ((start.get("pacing") or {}).get("act_check_frequency")) or 12
    offered_at = collections.defaultdict(list)
    for turn, key, surface in offer_events(kinds):
        offered_at[key].append((turn, surface))
    planted_at = {e["key"]: e["turn"] for e in kinds.get("waypoint", [])}
    cells = collections.defaultdict(lambda: [0, 0])  # (offered, carrier live) -> [n, planted within interval]
    surface_cells = collections.defaultdict(lambda: [0, 0])  # "act" | "nudge" | "none" -> [n, planted within interval]
    for e in kinds.get("funnel", []):
        if not e.get("check"):
            continue
        for row in e.get("carriers") or []:
            key, t = row["key"], e["turn"]
            recent = {surf for o, surf in offered_at.get(key, []) if t - interval < o <= t}
            offered = bool(recent)
            live = any(s in ("active", "progressed") for s in (row.get("carriers") or {}).values())
            hit = 1 if key in planted_at and t < planted_at[key] <= t + interval else 0
            cell = cells[(offered, live)]
            cell[0] += 1
            cell[1] += hit
            for surf in (("act",) if "act" in recent else ()) + (("nudge",) if recent & {"nudge", "drive"} else ()) + (() if offered else ("none",)):
                surface_cells[surf][0] += 1
                surface_cells[surf][1] += hit
    out = {"interval_turns": interval, "caveat": "association, not effect: see the module docstring",
           "cells": {f"offered={o},live_carrier={l}": rate(h, n) for (o, l), (n, h) in sorted(cells.items())},
           "by_surface": {k: rate(h, n) for k, (n, h) in sorted(surface_cells.items())}}
    for label, pick in (("offered", lambda o, l: o), ("not_offered", lambda o, l: not o)):
        n = sum(c[0] for (o, l), c in cells.items() if pick(o, l))
        h = sum(c[1] for (o, l), c in cells.items() if pick(o, l))
        out[label] = rate(h, n)
    return out


def _acts(kinds):
    checks = kinds.get("act_check", [])
    called = [e for e in checks if e.get("called") and not e.get("failed")]
    ready = [e for e in called if e.get("ready")]
    shown = collections.Counter()
    for e in called:
        for p in e.get("plants") or []:
            shown[p["key"]] += 1
    used = collections.Counter()
    for e in ready:
        for p in e.get("plants") or []:
            used[p["key"]] += 1
    counts = list(used.values())
    return {
        "checks": len(checks), "skipped_requires_unmet": sum(1 for e in checks if e.get("skipped") == "requires_unmet"),
        "called": len(called), "failed": sum(1 for e in checks if e.get("failed")), "ready": len(ready),
        "ready_rate": rate(len(ready), len(called)),
        "due": dict(collections.Counter(e.get("due") for e in called)),
        "acts_generated": len(ready),
        "turns_between_acts": med([b["turn"] - a["turn"] for a, b in zip(ready, ready[1:])]),
        "plants_per_check": {str(k): v for k, v in sorted(collections.Counter(len(e.get("plants") or []) for e in called).items())},
        "plant_chars": {"median": med([e.get("plant_chars", 0) for e in called if e.get("plant_chars")]),
                        "max": max([e.get("plant_chars", 0) for e in called] or [0])},
        "waypoints_offered": len(shown),
        "fairness": {"offers_min": min(counts) if counts else None, "offers_max": max(counts) if counts else None,
                     "distinct_used": len(counts)},
    }


def _carriers(kinds):
    scans = kinds.get("carrier_scan", [])
    started = [(e["turn"], s) for e in scans for s in e.get("started") or []]
    planted_at = {}
    for e in kinds.get("waypoint", []):
        planted_at[e["key"]] = e["turn"]
    rows = []
    for e in scans:
        for c in e.get("candidates") or []:
            if c["sid"] in (e.get("started") or []):
                after = planted_at.get(c["key"])
                rows.append({"sid": c["sid"], "turn": e["turn"], "for": c["key"], "planted_turn": after,
                             "turns_to_plant": (after - e["turn"]) if after is not None and after >= e["turn"] else None})
    unserved = [e for e in kinds.get("funnel", []) if e.get("check") for r in (e.get("carriers") or [])
                if not any(s in ("active", "progressed") for s in (r.get("carriers") or {}).values())]
    return {
        "scans": len(scans), "scans_with_candidates": sum(1 for e in scans if e.get("candidates")),
        "started_early": len(started), "started": rows,
        "steered_waypoint_checks_without_running_carrier": len(unserved),
        "steered_waypoint_checks_total": sum(len(e.get("carriers") or []) for e in kinds.get("funnel", []) if e.get("check")),
    }


def _threads(kinds, last_turn):
    changes = [c for e in kinds.get("threads", []) for c in e.get("changes") or []]
    gen = kinds.get("subplot_generated", [])
    return {
        "transitions": dict(collections.Counter(f"{c.get('from')}->{c.get('to')}" for c in changes)),
        "activated_by": dict(collections.Counter(c.get("why") for c in changes if c.get("to") == "active")),
        "generated": len(gen), "generated_per_100_turns": round(100 * len(gen) / last_turn, 2) if last_turn else None,
        "generated_titles": [g.get("title") for g in gen][:12],
    }


def _flags(kinds, start):
    events = kinds.get("flags", [])
    asked_turns = collections.defaultdict(list)
    set_turn, dropped = {}, collections.Counter()
    for e in events:
        for f in e.get("asked") or []:
            asked_turns[f].append(e["turn"])
        for f in e.get("set_declared") or []:
            set_turn.setdefault(f, e["turn"])
        for f in e.get("dropped_false") or []:
            dropped[f] += 1
    declared = start.get("declared_flags") or {}
    rows = {}
    for f in sorted(set(asked_turns) | set(set_turn) | set(declared)):
        first = min(asked_turns[f]) if asked_turns.get(f) else None
        rows[f] = {"has_detect": declared.get(f), "asked_turns": len(asked_turns.get(f, [])), "first_asked": first,
                   "set_turn": set_turn.get(f), "lag": (set_turn[f] - first) if f in set_turn and first is not None else None,
                   "dropped_false": dropped.get(f, 0)}
    su = [e.get("prompt_chars", {}).get("state_update") for e in kinds.get("turn", [])]
    chars = [e.get("asked_chars", 0) for e in events]
    return {
        "declared_with_detect": sum(1 for v in declared.values() if v), "set": len(set_turn),
        "never_set": sorted(f for f, r in rows.items() if r["has_detect"] and r["set_turn"] is None),
        "set_other_total": sum(e.get("set_other", 0) for e in events), "dropped_false_total": sum(dropped.values()),
        "asked_chars": {"median": med(chars), "max": max(chars or [0])},
        "share_of_state_update_prompt": round(sum(chars) / sum(x for x in su if x), 4) if events and any(su) else None,
        "rows": rows,
    }


def _nudges(kinds):
    nudges, directives = kinds.get("nudge", []), kinds.get("pacing_directive", [])
    fired = [e for e in directives if e.get("fired")]
    both = {e["turn"] for e in nudges} & {e["turn"] for e in fired}
    return {
        "nudges": len(nudges), "turns_between": med([b["turn"] - a["turn"] for a, b in zip(nudges, nudges[1:])]),
        "chars": {"median": med([e.get("chars", 0) for e in nudges]), "max": max([e.get("chars", 0) for e in nudges] or [0])},
        "parts": dict(collections.Counter(p for e in nudges for p in e.get("parts") or []).most_common(10)),
        "directives_fired": len(fired), "directives_deferred": sum(1 for e in directives if not e.get("fired")),
        "directive_rules": dict(collections.Counter(e.get("rule") for e in fired)),
        "turns_with_nudge_and_directive": len(both),
    }


def _clock(kinds, start):
    """CR-13, from the engine's own `clock` events (one per turn on a story that authors a story clock)."""
    events = kinds.get("clock", [])
    cfg = (start.get("pacing") or {})
    if not events:
        return {"authored": cfg.get("free_idle_streak") is not None, "turns": 0}
    idle = [e for e in events if e.get("idle")]
    runs, cur = [], 0
    for e in events:
        if e.get("idle"):
            cur += 1
        elif cur:
            runs.append(cur)
            cur = 0
    if cur:
        runs.append(cur)
    turns = kinds.get("turn", [])
    lean = sum(1 for t in turns if t.get("lean_forward"))
    pushes = kinds.get("push", [])
    last = events[-1]
    signal_counts = collections.Counter(s for e in events if not e.get("idle") for s in e.get("signals") or [])
    return {
        "authored": True, "free_idle_streak": cfg.get("free_idle_streak"), "has_push": cfg.get("has_push"), "turns": len(events),
        "idle": rate(len(idle), len(events)), "free_idle": sum(1 for e in idle if e.get("free")),
        "paid_idle": sum(1 for e in idle if not e.get("free")),
        "idle_runs": len(runs), "longest_idle_streak": max(runs or [0]),
        "streak_lengths": {str(k): v for k, v in sorted(collections.Counter(runs).items())},
        "moved_by": dict(signal_counts.most_common()),
        "pushes_fired": sum(1 for p in pushes if p.get("fired")), "pushes_yielded": sum(1 for p in pushes if not p.get("fired")),
        "lean_forward_turns": lean,
        "clock_lag_at_end": (max(e["turn"] for e in events) - last["clock"]) if last.get("clock") is not None else None,
        "idle_turns": [e["turn"] for e in idle][:30],
    }


def _nudge_steering(kinds):
    nudges = kinds.get("nudge", [])
    with_steering = [e for e in nudges if "mode" in e]
    carrier = [e for e in with_steering if e.get("carrier_plants")]
    hints = [e for e in with_steering if e.get("hint")]
    drives = [e for e in with_steering if e.get("drive")]
    yielded = [e for e in with_steering if e.get("drive_yielded_to")]
    flipped = [e for e in with_steering if e.get("primary") != e.get("primary_unboosted")]
    per_dest = collections.Counter(e["hint"]["dest"] for e in hints)
    per_key = collections.Counter(p["key"] for e in carrier for p in e["carrier_plants"])
    for e in drives:
        for k in e["drive"]["keys"]:
            per_key[k] += 1
    steering_chars = [e.get("steering_chars", 0) for e in with_steering]
    return {
        "nudges_with_steering_fields": len(with_steering), "nudges": len(nudges),
        "with_carrier_plant": rate(len(carrier), len(with_steering)),
        "plants_per_nudge": {str(k): v for k, v in sorted(collections.Counter(len(e.get("carrier_plants") or []) for e in with_steering).items())},
        "boost_changed_primary": rate(len(flipped), len(with_steering)),
        "with_hint": rate(len(hints), len(with_steering)), "hints_by_destination": dict(per_dest),
        "drive_nudges": len(drives), "drive_yielded": len(yielded),
        "drive_yielded_to": dict(collections.Counter(e["drive_yielded_to"] for e in yielded)),
        "drive_leaders": dict(collections.Counter(e["drive"]["leader"] for e in drives)),
        "waypoints_named": len(per_key), "named_per_waypoint": {"min": min(per_key.values()) if per_key else None,
                                                                  "max": max(per_key.values()) if per_key else None},
        "steering_chars": {"median": med(steering_chars), "max": max(steering_chars or [0]),
                           "share_of_nudge": round(sum(steering_chars) / sum(e.get("chars", 0) for e in with_steering), 3)
                           if with_steering and sum(e.get("chars", 0) for e in with_steering) else None},
    }


def _cost(kinds):
    turns = kinds.get("turn", [])
    step = collections.defaultdict(lambda: {"n": 0, "s": 0.0, "each": []})
    prompt = collections.defaultdict(list)
    for e in turns:
        for label, t in (e.get("timings") or {}).items():
            step[label]["n"] += t.get("n", 0)
            step[label]["s"] += t.get("s", 0.0)
            step[label]["each"].append(t.get("s", 0.0) / max(1, t.get("n", 1)))
        for label, chars in (e.get("prompt_chars") or {}).items():
            prompt[label].append(chars)
    narration_tokens = [round(c / 4) for c in prompt.get("narration", [])]
    return {
        "seconds": {k: {"calls": v["n"], "total": round(v["s"], 1), "p50": med(v["each"]), "p90": pct(v["each"], 90)}
                    for k, v in step.items()},
        "seconds_per_turn": round(sum(v["s"] for v in step.values()) / len(turns), 2) if turns else None,
        "prompt_chars": {k: {"n": len(v), "median": med(v), "p90": pct(v, 90), "max": max(v)} for k, v in prompt.items()},
        "narration_budget": rate(sum(1 for t in narration_tokens if t > NARRATION_TOKEN_BUDGET), len(narration_tokens)),
        "narration_tokens_max": max(narration_tokens or [0]),
    }


# --- many runs -------------------------------------------------------------------------------------------

def analyze(events, keep_regen=False):
    bad = sum(1 for e in events if e.get("kind") == "_bad_line")
    events = [e for e in events if e.get("kind") != "_bad_line"]
    if not keep_regen:
        events = drop_regenerated(events)
    runs = collections.OrderedDict()
    for e in events:
        runs.setdefault((e.get("story"), e.get("run")), []).append(e)
    out = [analyze_run(v) for v in runs.values()]
    for r in out:
        r["quality"]["bad_lines"] = bad  # a line that would not parse cannot be attributed to a run
    return out


def _fmt(value):
    if isinstance(value, dict) and "rate" in value:
        flag = "  (small n)" if value.get("small_n") else ""
        return f"{value['hits']}/{value['n']} = {value['rate']}{flag}" if value["n"] else "no data"
    return value


def render(runs):
    lines = []
    w = lines.append
    for r in runs:
        w("=" * 100)
        w(f"story {r['story']}  run {r['run']}  build {r['build']}  story_version {r['story_version']}")
        w(f"turns {r['turns']} (last {r['last_turn']}), events {r['events']}, run_start logged: {r['has_run_start']}, "
          f"regen events {r['quality']['regen_events']}, bad lines {r['quality']['bad_lines']}")
        f = r["funnel"]
        w("\n-- funnel")
        w(f"budget {f['budget']}  check_every {f['check_every']}  steer_top {f['steer_top']}  checks logged {f['checks']}")
        w(f"ended: {f['ended']}  turn {f['end_turn']}  cause {f['cause']}  ({f['title']})")
        w("phases: " + ", ".join(f"{p['phase']} {p['from']}-{p['to']}" for p in f["phase_turns"]))
        for d, s in sorted(f["scores"].items()):
            w(f"  score {d:24s} first {s['first']:.2f}  last {s['last']:.2f}  max {s['max']:.2f}  ({s['n']} checks)")
        w(f"steered set changed {f['steered_set_changes']}x; commit checks {f['commit_checks']}, judge said not-now "
          f"{f['judge_nulls']}, committed by the null limit {f['committed_by_null_limit']}; terminals tripped "
          f"{f['terminal_trips']} (confirmed {f['terminal_confirmed']})")
        for p in f["pruned"]:
            w(f"  pruned {p['dest']} at turn {p['turn']} ({p['why']})")
        wp = r["waypoints"]
        w("\n-- waypoints")
        w(f"planted {wp['planted']} of {wp['authored']} authored; route {wp['by_route']}; median plant turn "
          f"{wp['plant_turn_median']}; planted before any offer {wp['planted_before_any_offer']}")
        lag = wp["lag_after_first_offer"]
        w(f"lag from first offer to plant: median {lag['median']} p90 {lag['p90']} over {lag['n']}"
          f"{'  (small n)' if lag['small_n'] else ''}")
        if wp["never_planted"]:
            w("never planted: " + ", ".join(wp["never_planted"]))
        st = r["steering"]
        w(f"\n-- steering association (planted within {st['interval_turns']} turns of a funnel check)")
        w(f"  offered in the interval before:     {_fmt(st['offered'])}")
        w(f"  not offered in the interval before: {_fmt(st['not_offered'])}")
        for k, v in st["cells"].items():
            w(f"    {k:40s} {_fmt(v)}")
        for surf, v in st.get("by_surface", {}).items():
            w(f"    via {surf:34s} {_fmt(v)}")
        w(f"  {st['caveat']}")
        a = r["acts"]
        w("\n-- acts")
        w(f"act checks {a['checks']} (skipped, requires unmet {a['skipped_requires_unmet']}); called {a['called']}, "
          f"failed {a['failed']}, ready {a['ready']} -> {_fmt(a['ready_rate'])}; due {a['due']}")
        w(f"turns between acts (median) {a['turns_between_acts']}; plants per check {a['plants_per_check']}; plant text chars "
          f"{a['plant_chars']}; waypoints offered {a['waypoints_offered']}; fairness {a['fairness']}")
        c = r["carriers"]
        w("\n-- carriers")
        w(f"scans {c['scans']} ({c['scans_with_candidates']} with a candidate); started early {c['started_early']}; steered-"
          f"waypoint checks with no running carrier {c['steered_waypoint_checks_without_running_carrier']} of "
          f"{c['steered_waypoint_checks_total']}")
        for s in c["started"]:
            w(f"  turn {s['turn']}: started {s['sid']} for {s['for']}; that waypoint planted at {s['planted_turn']} "
              f"({s['turns_to_plant']} turns later)")
        t = r["threads"]
        w("\n-- threads")
        w(f"transitions {t['transitions']}; activated by {t['activated_by']}; generated {t['generated']} "
          f"({t['generated_per_100_turns']} per 100 turns)")
        fl = r["flags"]
        w("\n-- declared flags")
        w(f"with detect {fl['declared_with_detect']}, set {fl['set']}; never set: {fl['never_set']}; false reports dropped "
          f"{fl['dropped_false_total']}; undeclared set {fl['set_other_total']}; asked-text chars {fl['asked_chars']}; "
          f"share of the state-update prompt {fl['share_of_state_update_prompt']}")
        for name, row in fl["rows"].items():
            w(f"  {name:22s} asked {row['asked_turns']:>3} turns  first {row['first_asked']}  set {row['set_turn']}  lag {row['lag']}  "
              f"dropped_false {row['dropped_false']}")
        n = r["nudges"]
        w("\n-- nudges and directives")
        w(f"nudges {n['nudges']} every {n['turns_between']} turns, chars {n['chars']}, parts {n['parts']}; directives fired "
          f"{n['directives_fired']} deferred {n['directives_deferred']} {n['directive_rules']}; turns with both {n['turns_with_nudge_and_directive']}")
        ck = r["clock"]
        w("\n-- story clock")
        if not ck.get("turns"):
            w(f"authored: {ck.get('authored')}; no clock events in this run")
        else:
            w(f"free streak {ck['free_idle_streak']} (push authored: {ck['has_push']}); turns decided {ck['turns']}; idle {_fmt(ck['idle'])} "
              f"= {ck['free_idle']} free + {ck['paid_idle']} paid; runs {ck['idle_runs']}, longest {ck['longest_idle_streak']}, "
              f"lengths {ck['streak_lengths']}")
            w(f"turns that counted were moved by {ck['moved_by']}; pushes fired {ck['pushes_fired']} (yielded to a rule "
              f"{ck['pushes_yielded']}); options leaned forward on {ck['lean_forward_turns']} turns; story clock ended {ck['clock_lag_at_end']} behind")
            w(f"idle turns (read these in the transcript): {ck['idle_turns']}")
        ns = r["nudge_steering"]
        w("\n-- nudge steering")
        w(f"nudges with steering fields {ns['nudges_with_steering_fields']} of {ns['nudges']}; carrier plant on a thread line: "
          f"{_fmt(ns['with_carrier_plant'])}; plants per nudge {ns['plants_per_nudge']}; boost changed the lead thread: "
          f"{_fmt(ns['boost_changed_primary'])}")
        w(f"hints: {_fmt(ns['with_hint'])} by destination {ns['hints_by_destination']}; drive nudges {ns['drive_nudges']} "
          f"(leaders {ns['drive_leaders']}), yielded to a pacing rule {ns['drive_yielded']} {ns['drive_yielded_to']}")
        w(f"waypoints named in a nudge {ns['waypoints_named']} (per waypoint {ns['named_per_waypoint']}); characters added "
          f"{ns['steering_chars']}")
        k = r["cost"]
        w("\n-- cost")
        w(f"seconds per turn {k['seconds_per_turn']}")
        for label, v in k["seconds"].items():
            w(f"  {label:24s} calls {v['calls']:>4}  total {v['total']:>8}s  p50 {v['p50']}  p90 {v['p90']}")
        for label, v in k["prompt_chars"].items():
            w(f"  prompt {label:18s} n {v['n']:>4}  median {v['median']}  p90 {v['p90']}  max {v['max']}")
        if k.get("narration_tokens_max"):
            w(f"  narration budget: {NARRATION_TOKEN_BUDGET} tokens; turns over it: {_fmt(k['narration_budget'])}; "
              f"largest turn ~{k['narration_tokens_max']} tokens")
        w("")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("paths", nargs="*", help="trace .jsonl files (default: every file under data/traces)")
    ap.add_argument("--story"), ap.add_argument("--run")
    ap.add_argument("--keep-regen", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    paths = args.paths or sorted(glob.glob(os.path.join("data", "traces", "**", "*.jsonl"), recursive=True))
    if not paths:
        print("no trace files found (turn some turns first, or pass a path)", file=sys.stderr)
        return 1
    runs = analyze(load(paths), keep_regen=args.keep_regen)
    runs = [r for r in runs if (not args.story or r["story"] == args.story) and (not args.run or r["run"] == args.run)]
    print(json.dumps(runs, indent=2) if args.json else render(runs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
