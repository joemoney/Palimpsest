"""`endings` / `ending_funnel` - CR-05's ending funnel (Story_Mechanics_Update.md CR-05;
AUTHORING_TOOL_PHASES.md Phase S5, step 3). Built demand-first, against The Missing Core's
board-authored endings, which could not load until this engine was registered (D1).

**What this module owns, and what it deliberately does not.** It owns the funnel's *state*
and every *pure* decision about it: which waypoints are planted, which destinations are
pruned, how each viable destination scores, which ones are steered, which terminals have
tripped, and whether a commit is due. It does not own the two model calls a commit can need
(the commit judge and terminal confirmation), and it does not own how a story ends. Both
follow the seam `failure.py` and `detect_gate_refusal` already use: `resolve()` stays pure,
`story_engine.check_ending_funnel` makes the Tier C call and routes the result into
`_begin_endgame`, the one ending path. State writes that follow a judge call go through this
module's `record_*` functions so the shape of `mechanics.endings` in a save is defined here
and nowhere else.

**Phases** (CR-05), from `budget`, all optional:
  - *Open* (`turn < open_until`): every viable destination is steered.
  - *Narrow* (`open_until <= turn < narrow_until`): only the top `steer_top` by score.
  - *Commit window* (from `open_until`): at each check, a ready destination can be committed.
  - *Forced* (`turn >= commit_by`): the highest-scoring viable destination is committed.
A missing boundary means that phase never begins (no `commit_by`, no forced commit), except
that with no `open_until` the commit window is open from the start. No engine default stands
in for an authored boundary: when a story may end is a creative decision (CLAUDE.md, "No engine
constant may encode a creative decision").

**Polarity, per D2** (the same list `conditions.iter_conditions` declares): `viable_while` is
OPEN - an unknown referent must never prune, since pruning is permanent; `ready_when` and
waypoint `done_when` are CLOSED - an unknown referent must never commit an ending or plant a
waypoint.

**Waypoint keys are qualified**, `"<ending id>.<waypoint id>"`, the same spelling
`plot.subplots.*.delivers` uses: a waypoint id is only unique within its ending.

**One observation field** (`waypoints_hit`), and only when there is a pending waypoint with
`detect` text to ask about. `done_when` waypoints are checked in code and never asked. The
model sees numbered detect texts, never ending ids or names - the state-update pass is not the
narrator, but a number carries nothing that could leak into a scene later.
"""
from . import Effect, MechanicEngine, ObservationField, register, register_effect
import conditions

# Structural cadence, not a creative decision: how often the funnel re-scores. A story that
# wants a different rhythm authors `check_every`; none has to invent one to get a working
# funnel. `steer_top` is the same kind of default. Neither decides *when* a story may end -
# that is `budget`, which has no default at all.
DEFAULT_CHECK_EVERY = 6
DEFAULT_STEER_TOP = 2
# CR-05: after this many consecutive "not now" answers from the commit judge, the
# highest-scoring ready destination is committed without asking again.
JUDGE_NULL_LIMIT = 2
# CR-05's bound on the state-update pass: detect texts of at most this many pending waypoints.
MAX_DETECT_ASKED = 6
# CR-05's score: waypoint completion and ready_when proximity.
SCORE_WAYPOINTS, SCORE_READY = 0.6, 0.4


def waypoint_key(entry, waypoint) -> str:
    return f"{entry.get('id')}.{waypoint.get('id')}"


class EndingFunnel(MechanicEngine):
    slot = "endings"
    name = "ending_funnel"
    # Only orders this engine's one effect against the others', and it writes nothing another
    # engine reads. Before failure (90), which must stay last until D5 retires it. What the
    # funnel *reads* from this turn - stats moved, threads completed, flags set - it reads in
    # `settle()`, after every effect has applied, not here: resolve() sees the state as it was
    # before the turn's effects, which would lag every done_when by a turn.
    resolve_order = 85
    # §5.4: no narration text. Commitment is silent (CR-05) - the narrator learns the ending
    # only through the finale act `_begin_endgame` writes.
    prompt_budget = 0

    # --- configuration -------------------------------------------------------------

    def entries(self, cfg):
        entries = cfg.get("entries")
        if not entries:
            raise ValueError(
                "mechanics.endings declares engine 'ending_funnel' but authors no 'entries'. "
                "Every story needs at least one destination ending, including a catch-all.")
        return list(entries)

    def check_config(self, cfg):
        """CR-05's load-time invariant: at least one destination has no `viable_while`, so the
        funnel can never empty. Lint L08 reports the same thing to the author; this is the
        engine refusing to run a story that could reach a point with nowhere to go."""
        dests = self.destinations(cfg)
        if not any(not e.get("viable_while") for e in dests):
            raise ValueError(
                "mechanics.endings has no catch-all: every destination authors viable_while, so "
                "the funnel could prune them all and leave the story with nowhere to go. Leave "
                "viable_while off at least one destination.")

    def destinations(self, cfg):
        return [e for e in self.entries(cfg) if e.get("kind", "destination") == "destination"]

    def terminals(self, cfg):
        return [e for e in self.entries(cfg) if e.get("kind") == "terminal"]

    @staticmethod
    def check_every(cfg):
        return cfg.get("check_every") or DEFAULT_CHECK_EVERY

    @staticmethod
    def steer_top(cfg):
        return cfg.get("steer_top") or DEFAULT_STEER_TOP

    @staticmethod
    def budget(cfg):
        return cfg.get("budget") or {}

    # --- state ---------------------------------------------------------------------

    def init_state(self, cfg, ctx):
        return {"pruned": {}, "waypoints_done": {}, "scores": {}, "steered": [],
                "judge_nulls": 0, "terminal_cooldown": {}, "committed": None}

    def state(self, ctx):
        """This engine's bucket, read-only. A save created before the story authored endings
        has none; it reads as a fresh funnel rather than raising."""
        found = ((ctx.get("state") or {}).get("mechanics") or {}).get(self.slot)
        return found if isinstance(found, dict) else self.init_state({}, ctx)

    @staticmethod
    def turn(ctx):
        return ((ctx.get("state") or {}).get("pacing") or {}).get("turn_count", 0)

    def is_check_turn(self, cfg, ctx):
        turn = self.turn(ctx)
        return turn > 0 and turn % self.check_every(cfg) == 0

    def phase(self, cfg, ctx):
        """`open`, `narrow`, `commit` (narrow_until reached, commit_by not) or `forced`."""
        turn, budget = self.turn(ctx), self.budget(cfg)
        if budget.get("commit_by") is not None and turn >= budget["commit_by"]:
            return "forced"
        if budget.get("narrow_until") is not None and turn >= budget["narrow_until"]:
            return "commit"
        if budget.get("open_until") is not None and turn >= budget["open_until"]:
            return "narrow"
        return "open"

    def in_commit_window(self, cfg, ctx):
        open_until = self.budget(cfg).get("open_until")
        return open_until is None or self.turn(ctx) >= open_until

    def committed(self, ctx):
        return self.state(ctx).get("committed")

    # --- the funnel, read from state ----------------------------------------------

    def viable(self, cfg, ctx, pruned=None):
        pruned = self.state(ctx).get("pruned", {}) if pruned is None else pruned
        return [e for e in self.destinations(cfg) if e.get("id") not in pruned]

    @staticmethod
    def pending(entry, done):
        return [w for w in entry.get("waypoints") or [] if waypoint_key(entry, w) not in done]

    def score(self, entry, ctx, done):
        waypoints = entry.get("waypoints") or []
        completion = (1.0 if not waypoints else
                      sum(1 for w in waypoints if waypoint_key(entry, w) in done) / len(waypoints))
        ready = entry.get("ready_when")
        proximity = (conditions.evaluate(ready, ctx, conditions.CLOSED, ending=entry).proximity
                     if ready else 0.0)
        return round(SCORE_WAYPOINTS * completion + SCORE_READY * proximity, 4)

    def steered_ids(self, cfg, ctx, viable, scores):
        """Open: every viable destination. From Narrow on: the top `steer_top` by score, ties
        broken by authored order so the choice is deterministic."""
        if self.phase(cfg, ctx) == "open":
            return [e["id"] for e in viable]
        order = {e["id"]: i for i, e in enumerate(viable)}
        ranked = sorted(viable, key=lambda e: (-scores.get(e["id"], 0.0), order[e["id"]]))
        return [e["id"] for e in ranked[:self.steer_top(cfg)]]

    def steered(self, cfg, ctx):
        """The steered destinations as entries. Before the first check has stored a list, the
        phase rule is applied directly, so turn one is steered too."""
        st = self.state(ctx)
        viable = self.viable(cfg, ctx)
        ids = st.get("steered") or self.steered_ids(cfg, ctx, viable, st.get("scores") or {})
        by_id = {e["id"]: e for e in viable}
        return [by_id[i] for i in ids if i in by_id]

    def pending_detects(self, cfg, ctx):
        """`[(key, detect text)]` the state-update pass is asked about: pending waypoints with
        `detect` text on steered destinations, deduplicated by `plant` (CR-05: a waypoint shared
        by two destinations is written once under each), at most MAX_DETECT_ASKED."""
        done = self.state(ctx).get("waypoints_done") or {}
        out, plants = [], set()
        for entry in self.steered(cfg, ctx):
            for w in self.pending(entry, done):
                if not w.get("detect") or w.get("plant") in plants:
                    continue
                plants.add(w.get("plant"))
                out.append((waypoint_key(entry, w), w["detect"]))
        return out[:MAX_DETECT_ASKED]

    # --- observation ---------------------------------------------------------------

    def observations(self, cfg, ctx):
        if self.committed(ctx) or ctx["state"]["plot"]["endgame"]["requested"]:
            return None
        asked = self.pending_detects(cfg, ctx)
        if not asked:
            return None
        lines = "\n".join(f"  {n}. {text}" for n, (_, text) in enumerate(asked, 1))
        return [ObservationField(
            "waypoints_hit",
            '  "waypoints_hit": [<the NUMBER of each STORY MARKER below that this turn\'s scene '
            'clearly showed happening>]',
            f"\nSTORY MARKERS (number. what to look for):\n{lines}",
            "For waypoints_hit, list only markers the scene actually showed happening on the "
            "page, not ones merely hinted at or still to come. [] if none.\n",
        )]

    def events(self, cfg, ctx, diff):
        asked = self.pending_detects(cfg, ctx)
        out = []
        for n in diff.get("waypoints_hit") or []:
            try:
                index = int(n)
            except (TypeError, ValueError):
                continue
            if 1 <= index <= len(asked):
                out.append({"type": "waypoint_hit", "key": asked[index - 1][0]})
        return out

    # --- resolution ----------------------------------------------------------------

    def resolve(self, cfg, ctx, observations, events):
        """The model-observed half: waypoints this turn's scene showed (`detect`). Everything
        checked in code is `settle()`'s, run after this turn's effects have applied."""
        st = self.state(ctx)
        if st.get("committed") or ctx["state"]["plot"]["endgame"]["requested"]:
            return []
        done = dict(st.get("waypoints_done") or {})
        for event in observations or []:
            if event.get("type") == "waypoint_hit" and event.get("key") not in done:
                done[event["key"]] = self.turn(ctx)
        if done == (st.get("waypoints_done") or {}):
            return []
        return [Effect("endings.update", reason="waypoints_hit", waypoints_done=done)]

    def settle(self, cfg, ctx):
        """The code-checked half, after the turn's effects: plant `done_when` waypoints every
        turn; prune, score and re-steer at each check. Pure like resolve() - it returns one
        absolute effect for story_engine to apply, or none if nothing moved."""
        st = self.state(ctx)
        if st.get("committed") or ctx["state"]["plot"]["endgame"]["requested"]:
            return []
        turn = self.turn(ctx)
        done = dict(st.get("waypoints_done") or {})
        pruned = dict(st.get("pruned") or {})
        for entry in self.viable(cfg, ctx, pruned):
            for w in self.pending(entry, done):
                if w.get("done_when") and conditions.satisfied(w["done_when"], ctx, conditions.CLOSED):
                    done[waypoint_key(entry, w)] = turn

        scores, steered = st.get("scores") or {}, st.get("steered") or []
        if self.is_check_turn(cfg, ctx):
            for entry in self.viable(cfg, ctx, pruned):
                if self._ruled_out(entry, ctx, done):
                    pruned[entry["id"]] = turn
            # Scored against the ledger as it now stands: a waypoint planted this turn counts.
            view = {**ctx, "state": {**ctx["state"], "mechanics": {
                **(ctx["state"].get("mechanics") or {}), self.slot: {**st, "waypoints_done": done}}}}
            viable = self.viable(cfg, ctx, pruned)
            scores = {e["id"]: self.score(e, view, done) for e in viable}
            steered = self.steered_ids(cfg, ctx, viable, scores)

        update = {"waypoints_done": done, "pruned": pruned, "scores": scores, "steered": steered}
        if all(update[k] == (st.get(k) or ({} if k != "steered" else [])) for k in update):
            return []
        return [Effect("endings.update", reason="settle", **update)]

    def _ruled_out(self, entry, ctx, done):
        """A catch-all is never ruled out. Anything else is, when its `viable_while` goes false
        (OPEN), or when every uncompleted waypoint has carriers and all of them have failed
        (CR-10: "Lark walks out" pruning the_handover in code).

        The carrier rule is the conservative reading of CR-10's "carried only by failed
        threads": it prunes only when *no* remaining waypoint still has a live or unstarted
        carrier. A waypoint with no carrier at all is a storyboard gap, not a failure, so it
        never counts toward pruning."""
        viable_while = entry.get("viable_while")
        if not viable_while:
            return False
        if not conditions.satisfied(viable_while, ctx, conditions.OPEN):
            return True
        pending = self.pending(entry, done)
        if not pending:
            return False
        carriers = _carriers(ctx)
        statuses = ((ctx["state"].get("plot") or {}).get("subplots") or {})
        for w in pending:
            who = carriers.get(waypoint_key(entry, w), [])
            if not who or any((statuses.get(sid) or {}).get("status") != "failed" for sid in who):
                return False
        return True

    # --- commit decisions, read by story_engine.check_ending_funnel -----------------

    def ready(self, cfg, ctx):
        """Viable destinations whose `ready_when` holds (CLOSED). No `ready_when`, never ready:
        such a destination can still be reached, but only by a forced commit."""
        return [e for e in self.viable(cfg, ctx)
                if e.get("ready_when")
                and conditions.satisfied(e["ready_when"], ctx, conditions.CLOSED, ending=e)]

    def commit_due(self, cfg, ctx):
        return self.is_check_turn(cfg, ctx) and self.in_commit_window(cfg, ctx)

    def forced_due(self, cfg, ctx):
        return self.phase(cfg, ctx) == "forced"

    def leader(self, cfg, ctx, among):
        """Highest stored score among `among`, authored order breaking ties. Scores are only
        stored at checks, so an unscored entry is scored on the spot."""
        stored = self.state(ctx).get("scores") or {}
        done = self.state(ctx).get("waypoints_done") or {}
        order = {e["id"]: i for i, e in enumerate(self.destinations(cfg))}
        return min(among, key=lambda e: (-stored.get(e["id"], self.score(e, ctx, done)),
                                         order.get(e["id"], 0)))

    def tripped_terminals(self, cfg, ctx):
        """Terminals whose `ready_when` holds this turn (CLOSED), past `min_turn` and out of
        cooldown. Checked every turn, not at checks: a death should not wait for a cadence."""
        turn, cooldown = self.turn(ctx), self.state(ctx).get("terminal_cooldown") or {}
        return [e for e in self.terminals(cfg)
                if e.get("ready_when") and turn >= (e.get("min_turn") or 0)
                and turn >= cooldown.get(e.get("id"), 0)
                and conditions.satisfied(e["ready_when"], ctx, conditions.CLOSED, ending=e)]

    def final_arc(self, entry, bridging=None):
        """The finale act's title and description: the entry's authored `arc`, which becomes
        narrator-facing only now. With no arc authored, the ending's name alone - never its
        `criteria`, `hint` or any `_`-prefixed author note."""
        arc = entry.get("arc") or {}
        title = arc.get("title") or entry.get("name") or "The Ending"
        description = arc.get("description") or f"Bring the story to its ending: {title}."
        if bridging:
            description = f"{description}\n{bridging}"
        return {"title": title, "description": description}

    def bridging_note(self, cfg, ctx, entry):
        """A forced commit's bridge (CR-05): the leader's unplanted waypoints, folded into the
        finale. Built in code from the authored `plant` texts, which are narrator-facing
        already, rather than asked of a model - the engine knows exactly what is missing."""
        pending = self.pending(entry, self.state(ctx).get("waypoints_done") or {})
        plants = [w.get("plant") for w in pending if w.get("plant")]
        if not plants:
            return None
        return "Before the end, the finale must also bring these about: " + "; ".join(plants) + "."


def _carriers(ctx) -> dict:
    """`{waypoint key: [subplot ids that deliver it]}`, from the template."""
    out = {}
    for sid, sp in ((ctx["story"].get("plot") or {}).get("subplots") or {}).items():
        for key in sp.get("delivers") or []:
            out.setdefault(key, []).append(sid)
    return out


def _bucket(ctx):
    mech = ctx["state"].setdefault("mechanics", {})
    bucket = mech.get(ENGINE.slot)
    if not isinstance(bucket, dict):
        bucket = mech[ENGINE.slot] = ENGINE.init_state({}, ctx)
    return bucket


def _apply_update(ctx, effect):
    _bucket(ctx).update({k: effect.payload[k]
                         for k in ("waypoints_done", "pruned", "scores", "steered")
                         if k in effect.payload})


def record_commit(ctx, entry, forced=False):
    bucket = _bucket(ctx)
    bucket["committed"] = {"id": entry.get("id"), "turn": ENGINE.turn(ctx), "forced": bool(forced)}
    bucket["judge_nulls"] = 0


def record_judge_null(ctx) -> int:
    bucket = _bucket(ctx)
    bucket["judge_nulls"] = bucket.get("judge_nulls", 0) + 1
    return bucket["judge_nulls"]


def record_terminal_cooldown(ctx, cfg, entry):
    _bucket(ctx).setdefault("terminal_cooldown", {})[entry.get("id")] = (
        ENGINE.turn(ctx) + ENGINE.check_every(cfg))


ENGINE = register(EndingFunnel())
register_effect("endings.update", _apply_update)
