"""The engine trace: what the steering, ending-funnel and declared-flag machinery decided, turn by
turn, as data a reviewer can count.

Why this exists: the steering engine (docs: AUTHORING_TOOL_PHASES.md S5) rests on assumptions that no
test can settle - that a planted waypoint actually gets planted sooner, that declared flags are set
when their event happens and not otherwise, that the funnel's scores move the way the design says, that
an early-activated carrier is worth its cost. Prompt-content tests pass whether or not any of that is
true. So every decision the engine makes is written down at the moment it makes it, in a form
`scripts/steering_report.py` turns into numbers, and a reviewer can join to the save's own transcript
(`history.full_transcript` + `recent_turns`, by turn) to judge precision by eye.

**One JSONL file per save**, `data/traces/<user>/<story>.jsonl`, appended to and never rewritten. A line is
one event: `{"v", "ts", "run", "user", "story", "turn", "act", "kind", "regen", ...fields}`.

- `run` is a short id minted the first time a save takes a turn (`state["run_id"]`), so several
  playthroughs of one story in one file stay separable. `turn` is the turn *just completed* for every
  event of that turn (`pacing.turn_count` after its increment), so the events of one turn share a number.
- `regen` is true for events written while re-rolling the last turn. That turn's events appear twice
  under one `turn`; a reviewer drops the `regen` copy or the one before it, whichever the question wants.
- `build` (on `run_start`) names the steering feature set that produced the log, so a later run with more
  of the engine built can be compared with this one.

**Bounded, and a failure never reaches play.** The disk record may grow, but each event is small: strings
are clipped, lists and dicts capped (`_clip`), and full prompts are never written - only their sizes and the
ids of what they carried. Every write is wrapped: a full disk or an unwritable directory costs the trace,
never the turn. `PALIMPSEST_TRACE=0` turns it off entirely.

**What is deliberately not here:** player text and narration (they are in the save), model output beyond
the ids and outcomes the engine acted on, and any account detail. Events name the story's own ids
(waypoint keys, thread ids, flag ids): the file is an author-side record, like the save.
"""
import contextlib
import datetime
import json
import os
import sys
import threading
import uuid

import state_store

# The steering feature set writing this log. Bump when a piece lands, so two runs can be compared.
# steering-1: declared-flag detect, PLANT in act generation, early carrier activation.
# steering-2: + the nudge consumers: carrier priority and plant on a thread's line, hints, drive nudges.
# steering-3: + the story clock (CR-13): idle turns, the free streak, lean-forward options and the push.
TRACE_BUILD = "steering-3"
FEATURES = {"flags_detect": True, "plant": True, "early_activation": True,
            "carrier_nudge": True, "hint": True, "drive_nudge": True, "texture_only": False, "story_clock": True}

_MAX_STR, _MAX_ITEMS = 240, 60


class _Local(threading.local):
    user = None
    story = None
    regen = False
    notes = None
    before = None
    mute = 0


_local = _Local()
_warned = False


def enabled() -> bool:
    return os.environ.get("PALIMPSEST_TRACE", "1").lower() not in ("0", "false", "off", "no")


def bind(user_id, story_slug, regen=False) -> None:
    """Called where a turn starts (next to `_status_ctx`): who and what this thread is tracing."""
    _local.user, _local.story, _local.regen = user_id, story_slug, bool(regen)
    _local.notes = {"timings": {}, "prompt_chars": {}}
    _local.before = None


@contextlib.contextmanager
def muted():
    """Nothing is written inside this block. The Preview tab and the linter build real prompts on a copy of
    a story to show them; that is not play, and must leave no trace of it."""
    _local.mute += 1
    try:
        yield
    finally:
        _local.mute -= 1


def unbind() -> None:
    _local.user = _local.story = _local.notes = _local.before = None
    _local.regen = False


def path_for(user_id, story_slug) -> str:
    safe = lambda s: "".join(c if c.isalnum() or c in "-_." else "_" for c in str(s))  # noqa: E731
    return os.path.join(state_store.DATA_DIR, "traces", safe(user_id), f"{safe(story_slug)}.jsonl")


def _clip(value, depth=0):
    if isinstance(value, str):
        return value if len(value) <= _MAX_STR else value[:_MAX_STR - 1] + "…"
    if isinstance(value, dict):
        return {str(k): _clip(v, depth + 1) for k, v in list(value.items())[:_MAX_ITEMS]}
    if isinstance(value, (list, tuple, set)):
        items = sorted(value, key=str) if isinstance(value, set) else list(value)
        return [_clip(v, depth + 1) for v in items[:_MAX_ITEMS]]
    if isinstance(value, float):
        return round(value, 4)
    return value if isinstance(value, (int, bool)) or value is None else str(value)


def _fail(where, exc):
    global _warned
    if not _warned:
        _warned = True
        print(f"[TRACE] disabled for this process after a write failure in {where}: {exc!r}", file=sys.stderr)


def ensure_run(ctx) -> str:
    """The run id of this save, minted on first use (before the turn's pre-snapshot is taken, so a
    regenerate restores a state that already has it and the run does not split)."""
    state = ctx["state"]
    if not state.get("run_id"):
        state["run_id"] = uuid.uuid4().hex[:12]
        emit(ctx, "run_start", **_story_summary(ctx))
    return state["run_id"]


def emit(ctx, kind, **data) -> None:
    """Append one event. Never raises."""
    global _warned
    try:
        if not enabled() or _warned or _local.mute:
            return
        state = ctx["state"]
        user = _local.user or "local"
        story = _local.story or state.get("story_slug") or "unknown"
        rec = {"v": 1, "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds"),
               "run": state.get("run_id"), "user": user, "story": story,
               "turn": (state.get("pacing") or {}).get("turn_count", 0),
               "act": (state.get("plot") or {}).get("current_act"), "kind": kind, "regen": _local.regen}
        rec.update(_clip(data))
        line = json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n"
        target = path_for(user, story)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception as exc:  # noqa: BLE001 - tracing must never take a turn down
        _fail("emit", exc)


# --- per-turn accumulator: timings and prompt sizes, flushed as one `turn` event ---------------------------

def defer(ctx, kind, **data) -> None:
    """An event raised while a prompt is being built, before the turn's counter has moved. It is held and
    written when the turn is flushed, so it carries the number of the turn it belongs to."""
    try:
        if _local.mute:
            return
        if _local.notes is None:
            emit(ctx, kind, **data)
        else:
            _local.notes.setdefault("deferred", []).append((kind, data))
    except Exception as exc:  # noqa: BLE001
        _fail("defer", exc)


def note_timing(label, seconds) -> None:
    try:
        if _local.notes is not None:
            t = _local.notes["timings"].setdefault(label, {"n": 0, "s": 0.0})
            t["n"] += 1
            t["s"] = round(t["s"] + seconds, 3)
    except Exception as exc:  # noqa: BLE001
        _fail("note_timing", exc)


def note_prompt(label, chars) -> None:
    try:
        if _local.notes is not None:
            _local.notes["prompt_chars"][label] = _local.notes["prompt_chars"].get(label, 0) + int(chars)
    except Exception as exc:  # noqa: BLE001
        _fail("note_prompt", exc)


def stash_before(**values) -> None:
    """State captured at the start of a turn's update, for the diffs the end of the turn reports."""
    try:
        _local.before = dict(values)
    except Exception as exc:  # noqa: BLE001
        _fail("stash_before", exc)


def before() -> dict:
    return _local.before or {}


def flush_turn(ctx, **extra) -> None:
    try:
        notes = _local.notes or {"timings": {}, "prompt_chars": {}}
        for kind, data in notes.get("deferred", []):
            emit(ctx, kind, **data)
        emit(ctx, "turn", timings=notes["timings"], prompt_chars=notes["prompt_chars"], **extra)
        _local.notes = {"timings": {}, "prompt_chars": {}}
    except Exception as exc:  # noqa: BLE001
        _fail("flush_turn", exc)


# --- the run's configuration ------------------------------------------------------------------------------

def _story_summary(ctx) -> dict:
    """What a reviewer needs to interpret the rest of the file: the funnel's shape and the carrier map."""
    out = {"build": TRACE_BUILD, "features": FEATURES, "story_version": ctx["story"].get("story_version")}
    try:
        import mechanics
        plot = ctx["story"].get("plot") or {}
        pacing = plot.get("pacing") or {}
        out["pacing"] = {"nudge_frequency": pacing.get("nudge_frequency"),
                         "act_check_frequency": pacing.get("act_check_frequency"),
                         "max_parallel_subplots": pacing.get("max_parallel_subplots"),
                         "free_idle_streak": (pacing.get("story_clock") or {}).get("free_idle_streak"),
                         "has_push": bool((pacing.get("story_clock") or {}).get("push_directive"))}
        subplots = plot.get("subplots") or {}
        out["threads"] = {sid: {"role": sp.get("role", "spine"),
                                "start": "active" if sp.get("starts_active") else "when" if sp.get("activate_when") else "manual",
                                "delivers": list(sp.get("delivers") or [])} for sid, sp in subplots.items()}
        bound = mechanics.bound_for(ctx["story"], "endings")
        if bound is not None:
            cfg = bound.cfg
            out["endings"] = {
                "budget": dict(cfg.get("budget") or {}), "check_every": bound.engine.check_every(cfg),
                "steer_top": bound.engine.steer_top(cfg),
                "destinations": {e["id"]: {"catch_all": not e.get("viable_while"),
                                           "waypoints": [{"id": w.get("id"), "how": "done_when" if w.get("done_when") else "detect" if w.get("detect") else "none",
                                                          "has_plant": bool(w.get("plant"))} for w in e.get("waypoints") or []],
                                           "has_hint": bool(e.get("hint"))}
                                 for e in bound.engine.destinations(cfg)},
                "terminals": [e["id"] for e in bound.engine.terminals(cfg)]}
        block = (ctx["story"].get("mechanics") or {}).get("flags")
        if isinstance(block, dict):
            out["declared_flags"] = {f["id"]: bool((f.get("detect") or "").strip())
                                     for f in block.get("declared") or [] if isinstance(f, dict) and f.get("id")}
    except Exception as exc:  # noqa: BLE001
        out["summary_error"] = repr(exc)
    return out
