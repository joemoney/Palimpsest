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
import clock
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
# CR-05: "up to two unplanted waypoints" per act generation. Cadence of how much one prompt is asked to
# set up, not a creative decision, and it keeps that prompt text bounded.
MAX_PLANTS = 2
# CR-05's drive nudge names the leader's missing waypoints. Two, for the same reason as MAX_PLANTS: a scene
# can be pushed toward one or two things at once, and the nudge text stays bounded.
MAX_DRIVE = 2
# CR-10: an authored carrier is activated early at most this many at a time, per funnel check. A
# leader with three unplanted waypoints behind three dormant threads must not open all three at once.
MAX_EARLY_ACTIVATIONS = 1


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
                "judge_nulls": 0, "terminal_cooldown": {}, "committed": None, "offers": {},
                "nudge_offers": {}, "hints_shown": {}}

    def state(self, ctx):
        """This engine's bucket, read-only. A save created before the story authored endings
        has none; it reads as a fresh funnel rather than raising."""
        found = ((ctx.get("state") or {}).get("mechanics") or {}).get(self.slot)
        return found if isinstance(found, dict) else self.init_state({}, ctx)

    @staticmethod
    def turn(ctx):
        """The turn the budget, the checks and `min_turn` are measured in: the story clock (CR-13) when the
        story authors one, so free idle turns do not spend the ending budget, else `turn_count`. It is also
        what the funnel stamps into its ledger (`waypoints_done`, `pruned`, `committed`)."""
        return clock.story_turn(ctx)

    def is_check_turn(self, cfg, ctx):
        # `advanced`: on a free idle turn the story clock has not moved, so it may still sit on a multiple of
        # `check_every`. Without this a parked clock would re-run the check (and the commit judge) every idle turn.
        turn = self.turn(ctx)
        return turn > 0 and turn % self.check_every(cfg) == 0 and clock.advanced(ctx)

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

    # --- steering: what the act generator is asked to set up --------------------------

    def plant_candidates(self, cfg, ctx, limit=MAX_PLANTS):
        """`[{dest, key, plant, offers}]`: the unplanted waypoints of steered destinations that an act
        generation is asked to set up (CR-05's `PLANT`), at most `limit`.

        Only steered destinations contribute, so a pruned destination's waypoints never reappear and,
        from Narrow on, the ones that lost the ranking stop being pushed. A waypoint with no `plant`
        text has nothing to say and is skipped; one shared by two destinations is offered once
        (deduplicated by `plant`, as `pending_detects` does).

        **Fairness.** Without a memory the same two waypoints (the first of the first two destinations)
        would be offered every time until planted, and later destinations would never be set up. So the
        ranking is by how often a waypoint has already been offered to a generation that produced an act
        (`offers`, recorded by `record_offers`), then by its place in its own destination's pending list,
        then by destination order. The first generation gets the first waypoint of the first two
        destinations, the next gets the first of the next two, and so on round the table."""
        st = self.state(ctx)
        if st.get("committed") or ctx["state"]["plot"]["endgame"]["requested"]:
            return []
        done, offers = st.get("waypoints_done") or {}, st.get("offers") or {}
        ranked, seen = [], set()
        for d_index, entry in enumerate(self.steered(cfg, ctx)):
            place = 0
            for w in self.pending(entry, done):
                plant = (w.get("plant") or "").strip()
                if not plant or plant in seen:
                    continue
                seen.add(plant)
                key = waypoint_key(entry, w)
                ranked.append(((offers.get(key, 0), place, d_index), entry["id"], key, plant))
                place += 1
        ranked.sort(key=lambda r: r[0])
        return [{"dest": d, "key": k, "plant": p, "offers": offers.get(k, 0)} for _, d, k, p in ranked[:limit]]

    # --- steering: what the pacing nudge is asked to carry ------------------------------

    def nudge_plan(self, cfg, ctx):
        """What steering adds to a pacing nudge (CR-05, CR-10). Pure: `story_engine` composes the text and
        records what was used (`record_nudge`).

        `{"carriers": {sid: {key, dest, plant, nudged}}, "hint": {dest, text, shown} | None,
        "drive": {leader, waypoints: [{key, plant, via}]} | None}`, or all-empty once the story is ending.

        - **carriers**: each *running* thread that delivers an unplanted waypoint of a steered destination
          is handed one of them, the one it has been asked to carry least often (`nudge_offers`), then in
          authored order. The thread's nudge line carries the waypoint's `plant`, and the caller raises the
          thread's priority for the nudge. A plant already given to another thread is not repeated.
        - **hint**: one steered destination's `hint`, the one shown least often (`hints_shown`), then in
          steered order. At most one per nudge, so at most one per cycle. A destination with no hint has
          none to give.
        - **drive**: from `narrow_until` (the `commit` phase on), the leader among viable destinations
          (`leader`, by stored score) and up to MAX_DRIVE of its unplanted waypoints that have a `plant`,
          each with the running threads that could deliver it. The nudge presents these as the scene's
          priority; the caller drops it on a turn a pacing-loop rule fires (that rule wins).
        Only `plant` and `hint` text is ever produced: never an id, name, arc, criteria or `detect`."""
        empty = {"carriers": {}, "hint": None, "drive": None}
        st = self.state(ctx)
        if st.get("committed") or ctx["state"]["plot"]["endgame"]["requested"]:
            return empty
        done = st.get("waypoints_done") or {}
        nudged = st.get("nudge_offers") or {}
        carriers_of = _carriers(ctx)
        runtime = (ctx["state"].get("plot") or {}).get("subplots") or {}
        running = lambda sid: bool((runtime.get(sid) or {}).get("active"))  # noqa: E731

        offers = {}
        steered = self.steered(cfg, ctx)
        for entry in steered:
            for w in self.pending(entry, done):
                plant = (w.get("plant") or "").strip()
                if not plant:
                    continue
                key = waypoint_key(entry, w)
                for sid in carriers_of.get(key, []):
                    if running(sid):
                        offers.setdefault(sid, []).append((nudged.get(key, 0), len(offers.get(sid, [])), key, entry["id"], plant))
        assigned, used = {}, set()
        for sid in sorted(offers, key=lambda s: min(offers[s])[:2]):
            for nudged_n, _, key, dest, plant in sorted(offers[sid]):
                if plant not in used:
                    assigned[sid] = {"key": key, "dest": dest, "plant": plant, "nudged": nudged_n}
                    used.add(plant)
                    break

        shown = st.get("hints_shown") or {}
        hints = [(shown.get(e["id"], 0), i, e) for i, e in enumerate(steered) if (e.get("hint") or "").strip()]
        hint = None
        if hints:
            count, _, entry = min(hints, key=lambda h: h[:2])
            hint = {"dest": entry["id"], "text": entry["hint"].strip(), "shown": count}

        drive = None
        if self.phase(cfg, ctx) in ("commit", "forced"):
            viable = self.viable(cfg, ctx)
            if viable:
                leader = self.leader(cfg, ctx, viable)
                missing = [w for w in self.pending(leader, done) if (w.get("plant") or "").strip()][:MAX_DRIVE]
                if missing:
                    drive = {"leader": leader["id"], "waypoints": [
                        {"key": waypoint_key(leader, w), "plant": w["plant"].strip(),
                         "via": [sid for sid in carriers_of.get(waypoint_key(leader, w), []) if running(sid)]}
                        for w in missing]}
        return {"carriers": assigned, "hint": hint, "drive": drive}

    def carrier_report(self, cfg, ctx):
        """`[{key, dest, carriers: {thread id: status}}]` for every unplanted waypoint of a steered
        destination. Read-only; the trace's answer to "did the steered waypoints have a live carrier?"."""
        st = self.state(ctx)
        done = st.get("waypoints_done") or {}
        carriers = _carriers(ctx)
        statuses = ((ctx["state"].get("plot") or {}).get("subplots") or {})
        out = []
        for entry in self.steered(cfg, ctx):
            for w in self.pending(entry, done):
                key = waypoint_key(entry, w)
                out.append({"key": key, "dest": entry["id"],
                            "carriers": {sid: (statuses.get(sid) or {}).get("status", "not_started")
                                         for sid in carriers.get(key, [])}})
        return out

    def early_carriers(self, cfg, ctx):
        """`[{sid, key, dest}]`: authored threads worth activating early (CR-10), best first.

        From the Narrow phase on, a steered destination's unplanted waypoint whose carriers include
        no thread already running gets its first dormant authored carrier started, even though that
        thread's `activate_when` does not hold yet. Bounded by the caller to MAX_EARLY_ACTIVATIONS
        per check. Only a thread with an `activate_when` qualifies: one with none is the author's to
        start by hand (the Subplot Manager), and a texture thread carries nothing. A thread that has
        failed or completed is never restarted."""
        st = self.state(ctx)
        if st.get("committed") or ctx["state"]["plot"]["endgame"]["requested"]:
            return []
        if self.phase(cfg, ctx) == "open":
            return []
        done = st.get("waypoints_done") or {}
        carriers = _carriers(ctx)
        seeds = (ctx["story"].get("plot") or {}).get("subplots") or {}
        runtime = (ctx["state"].get("plot") or {}).get("subplots") or {}
        live = lambda sid: (runtime.get(sid) or {}).get("active") or \
            (runtime.get(sid) or {}).get("status") in ("active", "progressed")  # noqa: E731
        out, seen = [], set()
        for entry in self.steered(cfg, ctx):
            for w in self.pending(entry, done):
                key = waypoint_key(entry, w)
                who = carriers.get(key, [])
                if not who or any(live(sid) for sid in who):
                    continue
                for sid in who:
                    seed, rec = seeds.get(sid) or {}, runtime.get(sid) or {}
                    if sid in seen or seed.get("role") == "texture" or not seed.get("activate_when"):
                        continue
                    if rec.get("status", "not_started") != "not_started" or rec.get("active"):
                        continue
                    seen.add(sid)
                    out.append({"sid": sid, "key": key, "dest": entry["id"]})
                    break
        return out

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


def record_offers(ctx, keys):
    """Count a waypoint as offered once a generation that was told about it produced an act, so the next
    generation's ranking rotates (see `plant_candidates`)."""
    offers = _bucket(ctx).setdefault("offers", {})
    for key in keys:
        offers[key] = offers.get(key, 0) + 1


def record_nudge(ctx, keys, hint_dest=None):
    """Count what a pacing nudge carried, so the next one rotates: each waypoint plant it named and the
    destination whose hint it used. Recorded when the nudge is built, since a built nudge is a shown one."""
    bucket = _bucket(ctx)
    offers = bucket.setdefault("nudge_offers", {})
    for key in keys:
        offers[key] = offers.get(key, 0) + 1
    if hint_dest:
        shown = bucket.setdefault("hints_shown", {})
        shown[hint_dest] = shown.get(hint_dest, 0) + 1


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
