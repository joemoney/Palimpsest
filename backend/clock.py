"""CR-13: the story clock. Turns that move nothing don't spend the story's budget, up to a free streak.

(Story_Mechanics_Update.md CR-13 is the spec; this is the engine half of `plot.pacing.story_clock`.)

**Two clocks.** `pacing.turn_count` keeps counting every exchange and always has: the nudge cadence, the
flag-staleness window, the summary rollover and the recent-turns window (context bounds), the relationship
rate limits, every timestamp in the save, and the finale's own length. A second counter, `pacing.story_clock`,
counts the turns that moved the story plus every idle turn past the free streak, and it is what the ending
budget and its checks, the act-check cadence, `turn_gte` conditions and stat `per_turn` drift read. A story
that authors no `story_clock` has neither the counter nor any of this (P-2): every reader gets `turn_count`.

**Idle is decided in code, from observations every turn already produces** (P-7: never by asking a model "was
this idle?"). A turn is idle when the state-update pass reported none of: a thread moved past `touched`, a flag
set, a waypoint hit, a fragment revealed, a stat event or change, a social event, an item gained or used, a
leverage entry, a location change, a new named character. Those are read from the engines' own validated typed
events plus what the core state update applied, not from the model's raw JSON, so a claim the engine rejected
does not count. A turn inside the finale is never idle. A state-update pass that failed outright is not idle
either: an unknown turn must never be the one that comes free.

**The streak.** `idle_streak` counts consecutive idle turns and resets on any other. While it is at most
`free_idle_streak` an idle turn leaves the story clock unchanged; the next one advances it, and so does every
idle turn after that, so a run of k idle turns costs max(0, k - free_idle_streak) and every story still ends.
Once the streak is used up the options lean forward for as long as it holds, and `push_directive` (if authored)
joins the narration prompt once for that streak.

**A parked clock must not re-fire what keys on a value.** A funnel check is "the clock is a multiple of
`check_every`", stat drift is "the clock is a multiple of `per_turn_interval`", and on a free idle turn the clock
has not moved, so it still is. `advanced()` says whether the clock moved this turn and those readers require it.

This module is pure: no I/O, no model, no imports from the rest of the engine, so `conditions`, the mechanics
and `story_engine` can all read it.
"""

# What counts as the story moving, by the typed event that says so. `beat` (the pacing loop's classification of
# the scene) and `revelation_eligible` (a fragment that could be revealed, and was not) are deliberately not here.
_MOVERS = {
    "stat_event": "stat", "stat_changes": "stat", "social": "social",
    "item_gained": "item", "item_used": "item",
    "leverage_gained": "leverage", "leverage_spent": "leverage",
    "revelation_revealed": "fragment", "waypoint_hit": "waypoint",
}

LEAN_FORWARD = ("The player has been asking questions and looking around for a while. Offer choices that act, "
                "commit or go somewhere, not further questions.")


def config(story):
    """The authored `plot.pacing.story_clock` object, or None when the story does not author one."""
    block = (((story or {}).get("plot") or {}).get("pacing") or {}).get("story_clock")
    return block if isinstance(block, dict) and isinstance(block.get("free_idle_streak"), int) else None


def authored(ctx) -> bool:
    return config((ctx or {}).get("story")) is not None


def seed(story, pacing) -> None:
    """The counters a new save gets when its story authors a story clock (called by `new_save_state`)."""
    if config(story) is not None:
        pacing.update({"story_clock": 0, "idle_streak": 0, "push_fired": False, "clock_step": 1})


def ensure(ctx) -> None:
    """A save begun before the story authored a clock adopts one on its next turn, starting where the turn
    counter is, so nothing that already happened is re-timed."""
    if not authored(ctx):
        return
    pacing = ctx["state"]["pacing"]
    if "story_clock" not in pacing:
        pacing.update({"story_clock": pacing.get("turn_count", 0), "idle_streak": 0, "push_fired": False,
                       "clock_step": 1})


def story_turn(ctx) -> int:
    """The turn the ending budget, funnel checks, `turn_gte` and stat drift read."""
    pacing = ((ctx or {}).get("state") or {}).get("pacing") or {}
    if authored(ctx) and "story_clock" in pacing:
        return pacing["story_clock"]
    return pacing.get("turn_count", 0)


def advanced(ctx) -> bool:
    """Whether the story clock moved on the turn being processed. Always true for a story with no clock."""
    if not authored(ctx):
        return True
    return bool((((ctx.get("state") or {}).get("pacing")) or {}).get("clock_step", 1))


def signals(events, core) -> list:
    """The reasons this turn moved the story, in a stable order; empty means idle. `events` are the engines'
    typed events; `core` is what the core state update applied: `{"flag": bool, "place": bool, "character": bool}`."""
    found = set()
    for event in events or []:
        kind = event.get("type")
        if kind == "subplot_beat":
            if event.get("beat") != "touched":
                found.add("thread")
        elif kind in _MOVERS:
            found.add(_MOVERS[kind])
    found.update(name for name, moved in (core or {}).items() if moved)
    return sorted(found)


def advance(ctx, moved, finale=False) -> dict:
    """Decide this turn, once. `moved` is `signals(...)` (a list, empty when idle); `finale` makes it non-idle.
    Returns `{idle, free, signals, streak, clock, step}` for the trace."""
    ensure(ctx)
    pacing = ctx["state"]["pacing"]
    cfg = config(ctx["story"])
    idle = not moved and not finale
    if idle:
        pacing["idle_streak"] = pacing.get("idle_streak", 0) + 1
        free = pacing["idle_streak"] <= cfg["free_idle_streak"]
    else:
        pacing["idle_streak"], pacing["push_fired"], free = 0, False, False
    step = 0 if free else 1
    pacing["story_clock"] = pacing.get("story_clock", 0) + step
    pacing["clock_step"] = step
    pacing["clock_turn"] = pacing.get("turn_count", 0)
    return {"idle": idle, "free": free, "signals": list(moved), "streak": pacing["idle_streak"],
            "clock": pacing["story_clock"], "step": step}


def decided(ctx) -> bool:
    """Whether `advance` has already run for the turn being processed."""
    pacing = ctx["state"]["pacing"]
    return pacing.get("clock_turn") == pacing.get("turn_count")


def exhausted(ctx) -> bool:
    """The free streak is used up and still running: what the *next* narration prompt is told."""
    if not authored(ctx) or ctx["state"]["plot"]["endgame"]["requested"]:
        return False
    return ctx["state"]["pacing"].get("idle_streak", 0) >= config(ctx["story"])["free_idle_streak"]


def push_text(ctx):
    """The push directive, when it is due this turn: the streak is used up, one is authored, it has not fired for
    this streak, and the story is not ending. The caller marks it spent (`spend_push`) whether it uses it or
    yields it to a pacing rule."""
    if not exhausted(ctx) or ctx["state"]["pacing"].get("push_fired"):
        return None
    return (config(ctx["story"]).get("push_directive") or "").strip() or None


def push_due(ctx) -> bool:
    return exhausted(ctx) and not ctx["state"]["pacing"].get("push_fired")


def spend_push(ctx) -> None:
    ctx["state"]["pacing"]["push_fired"] = True
