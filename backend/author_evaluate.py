"""The authoring tool's sample-state evaluator (Authoring Tool decision D3; Phase S2).

"If the player were here, would this condition hold?" - answered by the real
`conditions.evaluate`, never by a parallel implementation. A **sample state** is what an author
types into the board's sample bar: a few stat values, some flags set, some fragments revealed. It
is expanded here into the same `{story, state}` shape the engine builds, so every leaf reads what
it reads at runtime, and every condition field in the template is evaluated at its declared
polarity (`conditions.iter_conditions`).

Pure: no Flask, no disk. `evaluate_all` runs the story through `author_model.playable_projection`
first, dropping any `mechanics` block this build cannot bind, so what is evaluated is what would
actually play - and a leaf that needs an unbuilt engine reads *unknown*, which the result shows
(and `left_out` names) rather than hides.

Sample shape (every key optional; anything omitted keeps the story's seeded value):

    {"stats": {"sync": 85}, "flags": ["lark_departed"], "revealed": ["frag_0002"],
     "relationships": {"Lark Ferris": 60}, "peaks": {"Lark Ferris": 70},
     "subplots": {"subplot_002": "completed"}, "waypoints_done": ["lark_aboard"],
     "tier_log": {"trace": ["loud"]}, "creation": {"trade": "listener"},
     "inventory_tags": ["writ"], "turn": 40, "act": 2}
"""
import copy

import author_model
import conditions
import mechanics


def _map(value) -> dict:
    """`value` if it is a dict, else {} - a sample is typed by hand and must never crash."""
    return value if isinstance(value, dict) else {}


def _list(value) -> list:
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []


def build_ctx(story: dict, sample: dict) -> dict:
    """`{story, state}` for `story` with `sample` laid over a freshly-seeded state. Never
    mutates its arguments."""
    sample = _map(sample)
    story = copy.deepcopy(story)

    seeded_stats = dict((story.get("protagonist") or {}).get("stats") or {})
    axes = ((story.get("mechanics") or {}).get("stats") or {}).get("axes") or {}
    floor = ((story.get("mechanics") or {}).get("stats") or {}).get("floor", 0)
    for axis in axes:
        seeded_stats.setdefault(axis, floor)
    stats = {**seeded_stats, **{k: v for k, v in _map(sample.get("stats")).items()
                                if isinstance(v, (int, float)) and not isinstance(v, bool)}}

    subplots = {}
    for sid, seed in ((story.get("plot") or {}).get("subplots") or {}).items():
        status = "active" if seed.get("starts_active") else "not_started"
        subplots[sid] = {"status": status, "progress": 0, "active": status == "active"}
    for sid, status in _map(sample.get("subplots")).items():
        if sid in subplots and isinstance(status, str):
            subplots[sid]["status"] = status
            subplots[sid]["progress"] = 1 if status in ("progressed", "active") else \
                (10 if status == "completed" else 0)

    characters = {}
    peaks = _map(sample.get("peaks"))
    for name, score in _map(sample.get("relationships")).items():
        if isinstance(score, (int, float)) and not isinstance(score, bool):
            entry = {"relationship": score}
            if isinstance(peaks.get(name), (int, float)):
                entry["peak"] = peaks[name]
            characters[name] = entry

    state = {
        "protagonist": {
            "stats": stats,
            "flags": {"active": {f: True for f in _list(sample.get("flags"))}, "archive": {}},
            "inventory": [{"label": t, "tags": [t]} for t in _list(sample.get("inventory_tags"))],
            "creation_choices": dict(_map(sample.get("creation"))),
            "leverage": [],
        },
        "plot": {
            "revelations_revealed": {r: {"turn": 0} for r in _list(sample.get("revealed"))},
            "current_act": sample.get("act") if isinstance(sample.get("act"), int) else 1,
            "subplots": subplots,
        },
        "pacing": {"turn_count": sample.get("turn") if isinstance(sample.get("turn"), int) else 0},
        "characters": characters,
    }
    if _map(sample.get("tier_log")):
        state.setdefault("mechanics", {})["stats"] = {
            "tier_log": {a: _list(l) for a, l in sample["tier_log"].items()}}
    if _list(sample.get("waypoints_done")):
        # The engine's ledger is keyed "<ending id>.<waypoint id>". The board's sample bar
        # sends bare waypoint ids, so a bare id counts as planted under every ending that has
        # one by that name; a qualified key is taken as it is.
        entries = (((story.get("mechanics") or {}).get("endings") or {}).get("entries") or [])
        ledger = {}
        for w in _list(sample["waypoints_done"]):
            if "." in w:
                ledger[w] = 0
                continue
            for e in entries:
                if any(isinstance(x, dict) and x.get("id") == w for x in e.get("waypoints") or []):
                    ledger[f"{e.get('id')}.{w}"] = 0
        state.setdefault("mechanics", {})["endings"] = {"waypoints_done": ledger}
    return {"story": story, "state": state}


def evaluate_all(story: dict, sample: dict) -> tuple:
    """`(rows, left_out)`. One row per condition field in `story`: `{path, polarity, satisfied,
    proximity, unknown, label}`, in template order; `label` is `conditions.describe`. `left_out`
    is the `(slot, engine)` pairs the projection dropped because this build does not have them.

    Conditions are read from the *un*-projected story (the author wrote them all, including the
    ones under an unbuilt engine); only the ctx they are evaluated against is projected."""
    projected, left_out = author_model.playable_projection(story, set(mechanics.registered_engines()))
    ctx = build_ctx(projected, sample)
    rows = []
    names = conditions.display_names(story)
    for path, cond, polarity, ending in conditions.iter_conditions(story):
        result = conditions.evaluate(cond, ctx, polarity, ending)
        rows.append({
            "path": path,
            "polarity": polarity,
            "label": conditions.describe(cond, names=names),
            "satisfied": result.satisfied,
            "proximity": round(result.proximity, 2),
            "unknown": result.unknown,
        })
    return rows, left_out


def stat_tiers(story: dict, sample: dict) -> dict:
    """`{axis: {"value": v, "tier": label-or-None}}` for the stat sidebar (Phase S3): each axis's
    value in the sample state and the tier the real engine puts it in (`BoundedCounter.tier_for`,
    through the bound engine - never a JS copy of the rule, D3). Empty when the projected story
    binds no stats engine, since then no tier line would reach the prompt either."""
    projected, _ = author_model.playable_projection(story, set(mechanics.registered_engines()))
    ctx = build_ctx(projected, sample)
    bound = next((b for b in mechanics.bind(projected) if b.slot == "stats"), None)
    if bound is None:
        return {}
    out = {}
    for axis, value in ctx["state"]["protagonist"]["stats"].items():
        tier = bound.engine.tier_for(bound.cfg, axis, value)
        out[axis] = {"value": value, "tier": tier.get("label") if tier else None}
    return out
